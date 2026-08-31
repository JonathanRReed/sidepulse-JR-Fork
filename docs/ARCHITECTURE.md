# JR Bar Architecture

Screen Bar profiling reuses the bounded `PresentationMetrics` registry in
`screen_bar_pipeline.py`. An explicit environment-gated export writes one
content-free runtime profile on normal termination. Instruments measurements
remain a separate receipt bound to a raw trace digest; they are not collected
or inferred by the normal application runtime.

The Screen Bar has one serial sampler and one JavaScriptCore batch path. Its
prefetch cache is scoped to the active generation, parsed program text, and
negotiated cadence. Any command replacement or timestamp mismatch invalidates
the remaining frames. Finite cues clamp the existing 24-frame ceiling to the
number of samples deliverable on or before their visual deadline. Runtime
metrics distinguish cache hits, shortened requests, invalidations, and engine
fallbacks; none of those counters is a hardware energy measurement.

`local_health.py` projects nine fixed, content-free current-run aggregates from
the existing `PresentationMetrics`, `PerformanceRegistry`, and
`RuntimeWorkerRegistry` owners plus numeric source ages. It keeps one bounded
interval baseline and peak queue count in memory. It does not retain provider,
agent, device, path, URL, credential, session, transcript, or event values, and
it has no persistence or network path. Refresh samples are taken only after the
refresh timing is recorded. The display callback acquires no new lock and does
no formatting or I/O for this projection.

The current-light explanation is split across four small boundaries.
`why_light_context.py` owns the immutable, bounded vocabulary and fixed-shape
copy. `why_light_projection.py` validates already-cached primitive facts.
`why_light_runtime.py` maps current controller state without I/O, retaining
only counts for the bounded current finite-cue plan. `why_panel.py` assembles
and updates selectable AppKit text while preserving a clamped selection and
scroll position. Scene stays explicitly unavailable until the scene system is
implemented. Surface scope is global, Focus observation failure remains
distinct from inactive Focus, and output timing carries an explicit source so
Screen Bar callback timing cannot be confused with physical write latency or
controller refresh duration.

This document records the boundaries, state owners, and invariants that are expensive to rediscover. Read it before changing the status-bar runtime, the display pipeline, or packaging.

## Product

JR Bar is a macOS menu-bar application and command-line tool that turns AI-agent activity into ambient light. It targets physical SidePulse LED devices mounted as USB volumes and an on-screen Screen Bar around the MacBook notch. Agent state shares the signal pipeline with notifications, calendar events, reminders, severe weather, battery state, timers, quota information, and user-authored LED programs.

## Controller boundary

The historical AppKit controller grew beyond 18,000 lines. It is retained in `status_bar_legacy.py` because a large mechanical rewrite would put working macOS behavior, permissions, timers, and device control at unnecessary risk.

`status_bar.py` is now the public compatibility facade. It preserves the existing import and monkeypatch contract, delegates the application entrypoint to the retained runtime, and replaces selected controller methods with implementations extracted into small modules. New deterministic behavior must not be added to `status_bar_legacy.py`. Extract it, test it without AppKit, then wire it through the facade.

The production controller is actually a THREE-deep chain, not two:
`status_bar_legacy.StatusBarController` → `_status_bar_production.JRStatusBarController`
(rebinds the legacy name) → `provider_usage_status_bar.JRProviderUsageStatusBarController`,
which the real entrypoint (`cli_entry` → `provider_usage_status_bar.main`) launches.
As of P2.24, that chain is assembled only by
`application_composition.compose_status_bar_application()`, which installs the
production controller, compatibility facade, settings navigation, Screen Bar
runtime, and provider-usage layer in one fixed order immediately before the
retained runtime creates its AppKit delegate. Ordinary imports of
`status_bar.py`, `_status_bar_production.py`, and
`provider_usage_status_bar.py` are now required to stay side-effect free with
respect to controller rebinding, menu rebinding, settings wiring, Screen Bar
bootstrapping, and device-refresh startup.

The first P2.25 extraction moved notification-action binding pruning,
content-free notification planning, completion-notification eligibility, and
token-to-work-key resolution into `notification_arbitration.py`. Notification
Center delivery, delegate callbacks, and session opening remain controller-owned
at the AppKit boundary.

The signal-selection slice moved the fixed 18-claim per-device precedence
table and asks-only muting metadata into `signal_selection.py`. The retained
controller now supplies live facts lazily and keeps only the once-per-display
diagnostic side effect. The selector owns ordering, short-circuiting, courtesy
muting, and the agent-display fallback without importing AppKit. The two
battery claims remain distinct identities even though both resolve to the same
display kind, so pinned or preview battery output cannot be confused with the
ambient charging-idle claim.

The effect-selection slice moved blend-mode, feel-preset, preview-scenario,
and provider-animation catalogs plus represented-object validation into
`effect_selection.py`. The shared Settings controls, Agents pane, and Color
Studio render those immutable catalogs and use one reverse-selection helper.
The retained controller and Studio action object consume pure selection plans,
then keep ownership of AppKit target/action dispatch, settings persistence,
refresh, status copy, Screen Bar previews, and physical-device previews. The
explicit Custom preset remains a valid no-op rather than a package of settings
to reapply.

The device-targeting slice removed an unreachable single-device chooser rather
than extracting dead policy. The live runtime already publishes a bounded
inventory, projects every connected candidate through `status_bar_devices()`,
creates agent and battery controllers per device id, and submits a distinct
opaque worker key per physical target. The retained controller no longer owns
scalar physical-device controllers, a preferred Pro-before-Dot priority table,
or a singular selected target. `current_led_targets()` is only a mounted-target
view over the live per-device controller maps for health and keepalive use.

The brightness slice moved ambient and signal brightness decisions into
`brightness_policy.py`. The pure module now owns the exact ambient factor
order, escalation visibility floor, Screen Bar minimum-glow floor, signal-path
minimum visibility floor, and the explicit Focus turn-off override for signals.
The retained controller still owns manual-versus-auto base selection,
display-brightness reads, idle timing, Focus observation, night-hour checks,
refresh triggering, and hardware writes.

The completion visibility slice moved clearable-row selection, unseen-completion
selection, visit acknowledgement planning, and clear-finished state planning
into `completion_visibility.py`. Current rows win stale duplicates for one
deterministic completion per agent id. The retained controller still owns menu
timestamps, activity-ledger writes, settings and service notifications,
controller assignment, signature invalidation, and refresh side effects.

The announcer slice now has two explicit boundaries. `announcer_content.py`
remains the stateless compatibility formatter for the old single-pill wording.
`announcer_stack.py` owns the bounded multi-alert state, stable first-seen
ordering, priority-only selection, exact generation fencing, and Screen-Bar-only
seen receipts. `announcer_stack_view.py` owns the passive collapsed pill, the
explicitly keyable expanded native panel, and typed Previous, Next, Open, Mark
Seen, and Close intents. `status_bar_legacy.py` keeps the controller-owned
stack state, exact status routing, and the one external side effect, opening the
selected session through the existing route. `virtual_device.py` keeps one
suppression predicate, one presenter instance, geometry, and lifecycle cleanup.
Mark Seen is presentation-local only. It never mutates local triage,
completion, mailbox, notification, or physical LED acknowledgement state.

P3.36 extends that same narrow product-owned ask surface with answer-in-place.
`answer_in_place.py` owns the pure local-answer capability and attempt model,
`answer_runtime.py` owns the bounded controller worker and stale-callback
fences, and the expanded Screen Bar and Agent Browser reuse the existing typed
operator-action path for inline reply, send, retry, cancel, timeout, and Jump
fallback. The answering surface remains explicit and capability-gated; it does
not grant implicit provider mutation authority or alter the independent LED,
triage, mailbox, notification, or release contracts.

P3.37 adds one named configurable global action without observing ordinary
typing. `global_actions.py` owns the immutable AppKit-free chord model,
validation, serialization, conflict projection, and strict persisted parsing.
`global_hotkeys.py` owns the lazy Carbon registration boundary, callback and
resource lifetime, transactional prepare/commit/rollback, and retryable cleanup.
`global_action_controller.py` owns durable rebind coordination and routes both
the visible menu command and shortcut through the same Reveal Current Ask path.
`global_action_settings_pane.py` owns the bounded first-responder recorder in
Overview Settings. The action toggles the current presentable announcer or opens
Agent Browser as a nonmutating fallback. It does not install an event monitor,
claim a default shortcut, or alter triage, mailbox, notifications, answers,
completion state, or LEDs.

P3.38 adds one immutable presentation policy for manual overrides, one daily
local-time schedule, public macOS Focus status, and optional named-Focus detail.
`dnd_policy.py` owns the five exact modes and composes each active source by
taking the strictest display, brightness, outbound, banner, audible, and
webhook value independently. Mute leaves every current visual claim visible but
refuses banners, sounds, and notification webhooks. Dim preserves visual and
outbound admission while scaling brightness. Pause admits critical visuals and
interruptions. Asks Only admits only current actionable asks and their
escalations. Fully Dark admits no presentation or outbound interruption and
keeps brightness at authoritative zero, below every minimum-glow and escalation
floor. None of these modes edits canonical agent, request, completion, history,
ingestion, persistence, or remote-sync truth.

`local_time_boundary.py` owns the shared local-wall-time resolver. The daily
schedule supports same-day and overnight intervals without adding a fixed 24
hours. A spring-forward gap advances to the first valid local second. A
fall-back fold selects the earliest valid epoch at or after the lower bound.
`dnd_controller.py` owns one transition timer, transactional Settings changes,
public Focus observation, generation fences, and lifecycle invalidation. A
temporary Resume suppresses only the local schedule, never an active macOS
Focus contribution. The controller recomputes on launch, app and session
activation, sleep and wake, screen sleep and wake, clock changes, time-zone
changes, and the bounded environment refresh.

`focus_status.py` lazily wraps the public `INFocusStatusCenter` API. Public
authorization plus coarse active or inactive state is authoritative. The
existing Full Disk Access-protected focusd reader may contribute stricter named
dim or signal detail only while authorized public Focus is active. Private
detail cannot activate DND, report public Focus inactive, or make missing Full
Disk Access look like a failure of the public integration. Permission requests
exist only behind the explicit Settings action.

The retained controller consumes one projection at every presentation and
effect boundary. Signal selection, ordinary agent display, physical LEDs,
Screen Bar, announcer, gauges, and brightness share its display and brightness
axes. Interruption arbitration produces separate banner, audible, and webhook
grants, so one effect is never nested under another effect's decision. Entering
Pause, Asks Only, or Fully Dark consumes finite cues, including cues armed by a
later asynchronous worker observation while the restrictive mode is already
active. Ending DND may reveal current standing truth, such as an unresolved ask
or failure, but it never replays an expired sweep, blink, preview, sound,
banner, webhook, or Screen Bar status cue.

`dnd_settings_pane.py` owns the retained native card inside Notifications &
Focus. It exposes the schedule, five modes, Dim fraction, public Focus status,
one-hour actions, temporary Resume, exact next change, accessibility metadata,
and one key-view loop. `menu_projection.py` and the `status_bar.py` compact
adapter expose the same typed mode, active sources, exact return time, Resume,
and End Override actions without restoring the deleted standalone Quiet row.
Why This Light and local health reuse their fixed-shape, content-free rows for
bounded DND mode, source, and return-time facts.

One coupling channel remains intentionally narrow rather than ambient:
`settings_window.py` now declares its dependencies through explicit imports plus
small cycle-safe helpers in `settings_window_controls.py`, while several modules
still take function-level imports to dodge the one real cycle
(`colors ↔ led_status ↔ _led_status_legacy`).

## Domain map

| Domain | Module(s) | State or responsibility |
| --- | --- | --- |
| Public runtime boundary | `status_bar.py` | Stable import surface, direct-module entrypoint, compatibility forwarding, narrow runtime patches |
| Historical AppKit runtime | `status_bar_legacy.py` | Window and menu lifecycle, timers, watchers, worker coordination, precedence integration, application assembly |
| Per-device projection | `device_projection.py`, `attention.py` | Canonical main/worker split, provider pin filtering, provider-local worker representative, lifecycle priority |
| Event ingestion | `hook_client.py`, `hook_ingress_protocol.py`, `hook_ingress.py`, `ipc.py`, `hook.py`, `collector.py` | Bounded ordered hook admission, canonical minimized writes, refresh hints, transcript fallback scanning, status collection, warm-start state |
| Compatibility entrypoints | `agent_monitor/`, `sidepulse_cli/` | Delegation for old installed hook module names; fail-open when arguments are missing |
| Latest-state clock codec | `latest_state_timing.py` | Per-source clock timing in the v2 snapshot, healing for pre-field documents |
| Usage pace | `usage_pace.py` | Burn-rate verdicts (surplus / on pace / fast / runs-out-before-reset) for rate-limit lanes |
| Canonical operator semantics | `operator_state.py`, `provider_facts.py`, `attention.py`, `mailbox.py` | Work identity, requests, transitions, parent/worker relationships, actionable attention |
| Signals and presentation | `signals.py`, `signal_coordinator.py`, `presentation_policy.py`, `presentation_scheduler.py` | Semantic precedence, finite cues, continuous state, interruption policy, schedule decisions |
| Rendering | `led_status.py`, `colors.py`, `animation.py`, `render_policy.py`, `brightness_policy.py` | LED programs, colors, transfer functions, motion, frame cadence, ambient and signal brightness policy, calibration |
| Screen Bar | `announcer_content.py`, `announcer_stack.py`, `announcer_stack_view.py`, `virtual_device.py`, `screen_bar_pipeline.py`, `screen_bar_runtime.py`, `screen_bar_design.py`, `alcove_observation.py` | Compatibility pill wording, multi-alert announcer state and projection, passive and expanded native presenter, notch geometry (measured silhouette), Alcove observation, frame scheduling, draw safety, on-screen rendering |
| Global actions | `global_actions.py`, `global_hotkeys.py`, `global_action_controller.py`, `global_action_settings_pane.py` | Immutable shortcut contracts, bounded Carbon registration, durable lifecycle transactions, one visible action route, and native Overview recorder |
| Do Not Disturb | `dnd_policy.py`, `local_time_boundary.py`, `focus_status.py`, `dnd_controller.py` | Five-mode dimensional policy, daily local-time schedule and DST boundaries, public coarse Focus authority, optional stricter named detail, one transition timer, Settings transactions, and lifecycle fences |
| Device I/O | `device_writer.py` → `presentation_compiler.py` → `firmware_validation.py` → `_device_writer_legacy.py`, `sd_eject_guard_launch.py` | Discovery, flash-safety compilation, firmware-grammar validation, size validation, atomic program writes, eject protection |
| Native provider usage | `provider_usage_*` modules (platform, runtime, sync service, status bar host, credential store, event store) | Claude/Codex quota accounting, Usage Center, cross-Mac SFTP usage sync, pairing, keychain-consented credentials |
| Agent Browser & history | `agent_browser.py`, `agent_browser_window.py`, `mailbox.py`, `operator_history*`, `activity_ledger*` | Session browser shelves and retention, mailbox ordering, operator history, "since you left" ledger |
| Remote & integrations | `remote_peers.py`, `cloud_ingest.py`, `webhook_delivery.py`, `t3_compat.py` | Peer Macs over SFTP, loopback cloud-event ingest, outbound webhooks, T3 Code local-state reads |
| Runtime scheduling | `runtime_scheduler.py`, `core_state.py`, `refresh_admission.py`, `adaptive_refresh.py` | Timer/worker registries, latest-wins workers, core-state observation, refresh admission, typed adaptive cadence plans, bounded menu-open admission receipts |
| Local health | `local_health.py`, `performance_metrics.py`, `screen_bar_pipeline.py` | Nine fixed current-run aggregates over existing in-memory timing, presentation, worker, and numeric freshness snapshots; no content, persistence, or export |
| Current-light explanation | `why_light_context.py`, `why_light_projection.py`, `why_light_runtime.py`, `why_panel.py` | Typed cached semantic and policy facts, bounded current-cue suppressions, explicit unavailable states, source-labeled output timing, and position-preserving selectable text; no probing, content identifiers, persistence, or telemetry |
| Power policy | `power_policy.py`, `keep_awake.py`, `lid_sleep.py`, `power_settings_pane.py` | Independent ordinary agent hold, display assertion, battery choice, closed-lid policy, and native settings projection |
| Firmware grammar | `led_wasm.py`, packaged `sdled.wasm` | Authoritative LED parser and animation stepping |
| Usage and capacity | `usage_stats.py`, `provider_capacity.py`, `capacity_*` modules | Local usage aggregation, provider evidence, authority gates, history, reset handling (the quota-forecast plane was deleted 2026-08-26) |
| Persistence | `persistence_writer.py`, `capacity_history_runtime.py`, `settings.py` → `_settings_legacy.py`, `*_store.py`, `private_io.py` | One bounded serial write owner, capacity-history lifecycle fencing, settings, ledgers, histories, atomic private-file writes, recovery from corrupt data |
| Settings UI | `settings_window.py`, `settings_category_runtime.py`, `settings_navigation.py`, `global_action_settings_pane.py`, `dnd_settings_pane.py` | Explicitly imported pane builders, seven-category IA, navigation, retained-control refresh, global-action recorder, and the native DND and Focus card |
| Packaging and launch | `app_bundle.py`, `status_bar_launch.py`, `packaging/` | Bundle identity helpers (the production bundle is built by `packaging/build_macos_pkg.sh`; the development-wrapper builder was deleted 2026-08-26), launch agent, signing, verification, installer and notarization |

Provider usage has an explicit worker-to-AppKit boundary. `ProviderUsageService`
loads settings, credentials, provider data, and locally cached cross-Mac evidence
on its serial worker. The callback publishes one immutable `ProviderUsageApply`
payload containing state, a presentation projection, and the durable usage
settings after refreshing the value-keyed sync memo. The presentation
projection cannot expose collection or sync choices. The separate durable
settings value keeps exact provider-instance identity available to Settings
checkboxes without reopening the document on the AppKit thread. Usage Center
rendering, menu projection, and settings-summary repaint consume only that
payload and memo. Initial restore and explicit settings saves remain bounded
I/O boundaries, but steady-state UI refresh does not reopen the settings
document or Keychain.

Adaptive provider refresh has a separate AppKit-free acceptance boundary in
`adaptive_refresh.py`. It explains the existing constrained, menu-recency,
idle, ambient-visibility, degraded-source, and reset-watch cadence with one
immutable plan and bounded reason. `ProviderUsageService` publishes the plan
only with an accepted revision-fenced refresh and exposes the latest accepted
value without I/O. The real AppKit menu-open path uses one admission helper to
record the provider-service visit and invoke the existing refresh planner;
provider collection remains worker-owned. Source tests pin freshness, backoff,
cadence precedence, and the caller-thread I/O boundary. They do not replace the
release gate's current 300-second Instruments evidence for installed idle CPU,
menu-open latency, or live menu-tracking I/O.

Provider usage identity is the composite `(provider_id, source_instance_id)`.
Legacy provider-only settings, consent, store, and sync rows migrate to the
explicit `default` instance. New settings, runtime snapshots, cached state,
sync projections, menu rows, Usage Center cards, browser consent, credentials,
reconnect, and refresh actions preserve that identity. Provider-only lookups
refuse ambiguity when two instances exist. The AppKit controller keeps only
selectors; exact action parsing and routing live in
`provider_usage_controller_actions.py`, which keeps the facade below its size
ratchet.

`ProviderInstanceProfile` is the bounded data contract for future per-instance
label, color, retention, remote-sharing, open-session, consent-reference, and
credential-reference persistence. In this checkpoint, labels flow through
usage snapshots and consent plus credentials are live. Color, retention,
remote-sharing, and open-session choices are validated and serializable, but do
not yet drive the retained color, history, remote-peer, or session-action
runtimes. That limitation is tracked explicitly rather than inferred complete.

Provider fixture provenance is a verification boundary, not runtime work. The
dated ownership manifest covers every first-party provider, binds each
synthetic fixture to a version and SHA-256 digest, rejects sensitive content,
and requires an explicit cross-provider allowlist. The validator is exercised
by the fast fixture lane and packaged-resource checks; the app does not import
or execute it during normal use.

History and reset-state persistence share one `SerialPersistenceWriter`. Ordered
usage-percent and operator-history appends remain FIFO; full-state reset and
capacity snapshots may replace only an equivalent pending command. Completion
receipts contain a command key and stable outcome, not stored content. Usage
percent watermarks advance only for successful append receipts. Capacity
history uses a store-generation fence so withdrawing consent cannot be undone
by an older queued flush. Normal producers observe a 64-command bound, while
one process-lifetime drain-tail slot is reserved for the final dirty capacity
snapshot immediately before `close()` stops acceptance and drains the queue.
The leaf stores still own private permissions, validation, fsync, retention,
corruption handling, and atomic replacement.

Power policy has two runtime owners. `KeepAwakeController` owns the ordinary
agent-driven system assertion and its release delay. `ClosedLidAwakeController`
owns the stronger closed-lid assertion, watchdog, renewal, and optional narrow
`pmset` helper. `power_policy.py` canonicalizes the `caffeinate` flags for both:
display sleep is allowed by default, and `d` is present only when the explicit
display setting is on. Changing that setting replaces only the affected
`caffeinate` child. It does not rewrite agent activity, battery yield,
closed-lid helper ownership, or watchdog state.

Physical LED work uses one bounded serial `LatestWinsWorker`. Ordinary frames
share one opaque `latest` slot per device, while persistent attention,
failures, finite cues, and explicit previews use separate priority-bearing
semantic slots. Priority can evict lower-priority pending or result work at the
32-slot bound, but it never bypasses deadlines, generation cancellation, or
close. Display kind is captured on the main thread so delayed work cannot
silently render a different claim. Physical calibration uses the same worker
and suppresses ordinary writes for that device. Detached lid flourishes first
advance the generation, cancel pending work, and wait a bounded interval for
the writer to become idle; timeout restores live state without starting a
second writer. All paths still terminate at the existing device safety
compiler and firmware validator.

Hook calls use a separate ordered admission boundary. New registrations invoke
the standard-library-only `hook_client.py`, which reads stdin once and sends a
versioned envelope to the same-user `0600` Unix socket at
`hook-ingress.sock`. Raw provider bytes can exist in the short-lived client,
socket buffers, and the app's bounded 32-event memory queue. They are never
written as ingress receipts. One app-owned FIFO worker sends accepted events
through the same routing, normalization, minimization, cross-process dedupe,
private JSONL write, and compaction used by the synchronous entry point. Because
this worker already runs inside the monitor-owning app, it applies the refresh
callback in-process and synchronously. Standalone fallback hooks still use the
private refresh-hint socket after their canonical write.

Admission returns one exact outcome. Accepted work is not retried by the
client. Full, closed, and invalid refusals are recorded without event content
at `hook-ingress-rejections.jsonl`; retrying them synchronously would let a
newer event pass older accepted work. If no ingress socket is available, the
client loads the canonical processor and writes synchronously so JR Bar being
closed does not disable provider logs. Normal app termination closes admission,
drains accepted hook work through monitor reconciliation, persists latest state,
and only then stops the refresh-hint server. A drain timeout records every
accepted sequence that could not finish.
If a trusted socket accepted the connection but its acknowledgement is lost,
the client reports submission as ambiguous and does not run fallback. The
server may already own that event, so retrying would duplicate or reorder it.
This favors ordered at-most-once submission over hiding an uncertain update.

## Provider instance profiles

Provider usage settings own five non-secret choices for each exact
`(provider_id, source_instance_id)` pair: label, color override, native
percentage-history retention, outbound remote-sharing choice, and session-open
action. Browser consent and credential references remain in their dedicated
stores and are absent from consumer projections.

`provider_feature_settings.py` projects separate immutable visual, retention,
sharing, and session-action views. UI paths consume the cached projections and
do not reopen settings during repaint. An explicit Settings action may load and
save once, then atomically refreshes the durable snapshot, consumer
projections, cached card values, accessibility labels, and menu signature.
Default-instance session opening continues through the legacy provider/origin
router. A non-default override applies only after an exact work-source match.

Native percentage history uses the same exact identity for dedupe, retention,
pruning, and graph series. Outbound sharing fails closed. `never` excludes an
instance, while `status_only` emits bounded quota and source-status evidence
without tokens, costs, account labels, model counts, or machine usage. Cached
cross-Mac evidence is memory-only on UI paths, is fenced by the non-secret
sharing signature, and expires after a short monotonic lease.

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

Alcove following uses one typed, AppKit-free confidence projection shared by
Settings, Doctor, and the Screen Bar. The source contract distinguishes fresh,
stale, permission denied, disconnected, unsupported, not following, and
recovering; held geometry expires on the existing bounded timer. Recovery gets
one on-screen ease-out settle, replaced by an immediate frame when Reduce
Motion is enabled. Native source renders and accessibility metadata are
receipts for this contract only, not installed-app, permission, live-Alcove,
physical-display, signing, notarization, or release proof.
`alcove_settings_pane.py` owns the native Alcove row construction,
accessibility metadata, refresh application, and legacy compatibility mapping;
`settings_window.py` retains only the injectable projection adapter, action
boundary, and public compatibility functions.

## Invariants

- Never emit `N:off` in an indexed LED DSL segment. Use `#000000`. The firmware parser treats the former as an error.
- `validate_led_text` validates size, not grammar. User-authored programs must pass through `SdLedWasmController.parse()`.
- `NSColorWell` is not used in this PyObjC host. Use swatches and the classic `NSColorPanel` route.
- Screen Bar geometry is derived from measured screen pixels. Alcove windows are observations, not authoritative notch geometry.
- Screen Bar prefetch is command-scoped. Never reuse a batch across a generation, parsed program, cadence, or timing discontinuity.
- Settings and private state writes are atomic, uniquely named, permission-restricted, and recoverable. Two writers must never share one scratch path.
- Accepted history and reset-state writes are serialized and drained on normal termination. Queue refusal and bounded shutdown timeout are explicit outcomes, not silent success.
- TCC grants belong to the sealed application identity. Ad-hoc or differently signed builds are different applications and lose permission continuity.
- Background watchers fail quietly, back off, and always release their in-flight state.
- Hook entrypoints fail open. Socket unavailability uses synchronous canonical processing; explicit bounded-queue refusal is recorded and never retried ahead of accepted work. A stale compatibility command may lose one update, but it must never block the user's agent session.
- Physical-device writes are isolated from tests. Controller tests must replace settings, latest-state paths, and device discovery before construction.
- Device coalescing keys are bounded and content-free. Paths, provider payloads, session labels, and effect source text never enter worker keys or metrics.
- A requested value, an assumed value, and the value delivered by AppKit or hardware must be reconciled. Frame rate, window geometry, signing identity, and provider evidence all follow this rule.

## Verification and release

The authoritative gate is `./scripts/verify.sh` on macOS. It installs the fork in an isolated development environment, runs Ruff, validates versions, executes the complete test suite, builds distributions, checks metadata, and installs the wheel into a fresh virtual environment. `./scripts/verify.sh --portable` runs the platform-neutral rescue gate elsewhere.

GitHub Actions are manual-only while hosted minutes are unavailable. A release is created locally from the owner's Mac through `scripts/publish_release.sh`. The script requires a clean `main`, matching source/package/changelog versions, complete verification, Developer ID Application and Installer signing, notarization, candidate-bound receipts, checksums, and a GitHub Release. `scripts/verify_macos_release.sh` re-hashes one exact PKG, app tree, supplemental Sparkle ZIP, signed appcast, and channel document; validates the app and PKG notarization logs against their submitted digests; binds package and archive contents to the app; and records upgrade, supported uninstall, and clean-reinstall evidence before manifest assembly. The compatibility-named PKG remains the authoritative installer and recovery path. Pinned Sparkle 2.9.6 provides the consent-driven update path through a durable signed feed with stable and beta channels. This fork does not automatically publish the upstream-owned `sidepulse` project name to PyPI.

## Deliberate debt

- `status_bar_legacy.py` remains large. Extract one pure decision boundary at a time, with regression tests and a facade wiring change in the same commit.
- Existing file-specific Ruff exceptions document inherited ordering debt. Do not add new exceptions for extracted modules.
- The complete AppKit, TCC, signed-package, and physical-hardware gates require macOS. A portable pass is necessary but not sufficient for release.
- Upstream changes are reviewed behavior by behavior. Do not merge the upstream controller wholesale into the divergent fork.
