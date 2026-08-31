# JR Bar master roadmap and ambient effects design

Date: 2026-08-28

Status: Proposed for owner review. This document is a design, not an implementation receipt.

## Purpose

JR Bar should become a trustworthy ambient computer-awareness system. A person should be able to understand what their agents and connected computer are doing without opening a dashboard, reading private content, or learning an arbitrary color code. The same semantic event should be expressed appropriately on the Screen Bar, SidePulse Pro, SidePulse Dot, the menu bar, and the optional notification light.

This specification retains all 66 recommendations from the August 28 audit. Six effect-system recommendations are inserted at their proper priority, producing a 72-item master roadmap.

## Locked product decisions

1. The human-facing product name is **JR Bar**.
2. The `sidepulse` CLI, `io.sidepulse.*` identifiers, existing support directories, Keychain services, and SidePulse hardware names remain stable until a separately tested migration exists.
3. JR Bar remains local-first. Cloud telemetry, prompt collection, and transcript retention are not required for ambient awareness.
4. Words belong in the announcer and browser. Physical LEDs carry compact state, identity, urgency, and transition cues.
5. Color is never the only state channel. Motion, position, geometry, cadence, and text alternatives must carry the same meaning.
6. Asking, failure, and user-directed notification semantics override decorative provider motion.
7. Continuous effects remain calm. Celebrations and transitions are finite. Nothing exceeds the existing flash and courtesy budgets.
8. Preview is reversible. Hovering or testing an effect must never save settings, install hooks, disclose content, or leave hardware pinned after the preview ends.
9. The existing safety compiler, firmware parser, render policy, and held-preview session remain authoritative. The new design extends them rather than creating a second renderer.
10. No feature is release-verified until the exact signed artifact passes installed-app, hardware, accessibility, privacy, and performance gates.

## External inspiration retained in this design

- SidePulse [PR #37](https://github.com/inteliwear/sidepulse/pull/37) for consistent XDG socket resolution
- SidePulse [PR #36](https://github.com/inteliwear/sidepulse/pull/36) for DisplayServices brightness
- SidePulse [PR #33](https://github.com/inteliwear/sidepulse/pull/33) for trailing-edge device-write coalescing
- SidePulse [PR #32](https://github.com/inteliwear/sidepulse/pull/32) for hook-latency evidence, adapted here to preserve ordering
- SidePulse [PR #31](https://github.com/inteliwear/sidepulse/pull/31) for display-sleep-aware keep-awake behavior
- SidePulse [PR #28](https://github.com/inteliwear/sidepulse/pull/28) for manual and scheduled DND
- SidePulse [PR #27](https://github.com/inteliwear/sidepulse/pull/27) for live remote observation, adapted here without a general remote-command surface
- SidePulse [PR #34](https://github.com/inteliwear/sidepulse/pull/34) for push-secret hardening
- SidePulse [PR #19](https://github.com/inteliwear/sidepulse/pull/19) for invocation-scoped provider monitoring
- [adamstambouli/sidepulse](https://github.com/adamstambouli/sidepulse) for sticky fleet bands
- [T3 Code internals](https://github.com/pingdotgg/t3code/blob/main/docs/internals/overview.md) for provider instances, serialized commands, receipts, projectors, and drainable workers
- [CodexBar refresh design](https://github.com/steipete/CodexBar/blob/main/docs/refresh-loop.md) for adaptive refresh behavior
- [CodexBar release process](https://github.com/steipete/CodexBar/blob/main/docs/RELEASING.md) for candidate-bound release and updater evidence

These sources are inspiration, not merge targets. JR Bar adapts each idea to its current privacy, hardware, local-first, and compatibility boundaries.

## Considered approaches

### Approach A: One semantic effect system, recommended

Events are normalized into semantic signals such as working, asking, completed-unseen, failed, recovered, handed-off, quota-reset, and idle. One registry describes effects. Surface renderers adapt each signal to Screen Bar, Pro, Dot, Glance Light, menu bar, and notifications. This keeps meaning consistent while respecting different LED counts and capabilities.

### Approach B: Extend each surface independently

This would be faster for the first few effects, but Screen Bar, Pro, Dot, and menu behavior would drift. Preview, Reduce Motion, priority arbitration, and settings would be duplicated. This approach is rejected.

### Approach C: User-scripted effect plugins

This offers maximum flexibility but introduces executable-code, stability, privacy, and firmware-safety risks. JR Bar can support shareable data-only effect packs later. Arbitrary executable effect plugins are rejected.

## System architecture

### Semantic signal router

Provider events, usage events, local system state, calendar and reminder events, weather, device state, and explicit user notifications enter one router. The router emits semantic intentions rather than firmware programs.

Default priority:

1. An unresolved question that explicitly needs the user
2. A failure that needs intervention
3. A user-directed Glance Light notification
4. Active work and handoff transitions
5. An unseen completion or recovery
6. Quota, battery, calendar, reminder, and weather courtesy signals
7. Environmental ambience and idle presence

The router applies DND, snooze, Focus, night dimming, low-power mode, thermal policy, courtesy limits, Reduce Motion, device roles, acknowledgements, and expiration before selecting an effect.

### Effect registry

Every built-in and imported effect has one stable identifier and metadata:

- Name, plain-language purpose, and semantic family
- Supported surfaces and minimum LED topology
- Finite, continuous, one-shot, or one-shot-plus-loop behavior
- Default duration, speed range, brightness range, direction, and density
- Interruptibility and priority behavior
- Energy class and expected frame cadence
- Reduce Motion substitute
- Color-blind-safe and low-vision behavior
- Firmware grammar requirements and parser version
- Preview scenarios and deterministic test seed
- Version and migration behavior

Registry entries compile through the existing safety compiler and real firmware parser. An effect pack can select and parameterize registered primitives, but cannot execute code.

### Surface renderers

- **Screen Bar:** words, agent segmentation, liquid transitions, notification orb, and richer spatial animation.
- **SidePulse Pro:** fleet detail, provider or project segments, finite transitions, and an optional reserved endpoint for Glance Light.
- **SidePulse Dot:** compact two-light semaphore, asking, failure, unseen completion, and fleet-size abstractions.
- **Glance Light:** a tiny, persistent, low-energy notification indicator inspired by early Android notification LEDs. It can appear on compatible physical hardware, at a Screen Bar endpoint, and as an optional menu-bar accent.
- **Menu and Agent Browser:** exact labels, message-safe explanations, acknowledgement controls, history, and configuration.

### Effect Studio and Preview Lab

The current Studio becomes the single place to understand, preview, assign, compare, and manage effects.

It provides:

- A searchable gallery grouped by Working, Asking, Completion, Failure, Recovery, Notification, Quota, Environment, Idle, and Transition
- A live card for every effect showing what it means, when it runs, supported surfaces, duration, energy class, and Reduce Motion behavior
- Side-by-side Screen Bar, Pro, Dot, and Glance Light simulation
- Synthetic scenarios for one agent, several agents, asking, failure, handoff, completion, quota reset, DND, low power, sleep, lid transitions, and remote fleet changes
- Timeline scrubbing, pause, replay, reduced-motion preview, and color-vision simulation
- Before-and-after comparison against committed settings
- Assignment scope for global defaults, semantic state, provider, provider instance, project, device, and Scene
- Explicit, bounded physical-hardware preview with a visible “Previewing, not saved” state and guaranteed release on close, sleep, app termination, or error
- Reset, duplicate, rename, import, export, and restore-default actions
- A “Why this effect?” inspector showing source freshness, priority, suppressed signals, policy decisions, and expiration

## Final prioritized roadmap

### P0: Correctness, privacy, identity, and release blockers

1. **Repair canonical gate truth.** Replace the secret-like fixture flagged by the secret scanner. Make release-version parsing accept titled changelog headings. Add regression tests that execute both gates rather than matching their source text.

2. **Unify agent-monitor socket resolution.** Use one XDG-aware resolver for hooks, CLI, app, LaunchAgent, doctor, installer, and tests. Verify an event written by a hook reaches the app under default and custom `XDG_STATE_HOME` environments.

3. **Use trustworthy display brightness.** Read Control Center brightness through DisplayServices, retain a bounded IOKit fallback, report unknown instead of pretending full brightness, and test external-display and sleep transitions.

4. **Remove secrets from Claude process arguments.** Prefer read-only observation, invocation-scoped authorization, or JR Bar-owned ephemeral material. Make credential ownership, refresh responsibility, rollback, and Keychain access explicit.

5. **Enforce exact Devin consent.** Store and validate provider, browser, profile, domain, and field as one consent identity. Never broaden an approval silently when a profile or browser changes.

6. **Secure loopback state serving.** Require a local bearer credential or expose a deliberately redacted default schema. Hide account identity, costs, private session labels, and message content unless separately authorized. Never return a server secret in a status response.

7. **Centralize JR Bar product identity.** Visible titles, notifications, help, settings, Usage Center, release notes, and accessibility labels say JR Bar. Hardware remains SidePulse Pro and SidePulse Dot. Compatibility identifiers remain stable.

8. **Choose an authoritative release artifact.** Decide whether ZIP, PKG, or both are supported products. Align certificate requirements, documentation, publisher behavior, updater metadata, artifact names, and checksums with that decision.

9. **Bind release evidence to the exact candidate.** Record commit, artifact hash, signing identity, notarization, stapling, Gatekeeper, SBOM, package contents, clean install, upgrade, and uninstallation behavior in one manifest.

### P1: Performance, accessibility, and operational reliability

10. **Profile the Screen Bar on real hardware.** Capture Instruments evidence for callback time, JavaScriptCore calls, batch hit rate, delivered frames, wakeups, energy, memory, and thermal state under static, working, asking, multi-agent, DND, low-power, and hidden conditions.

11. **Improve the existing frame batching.** Do not add a parallel batcher. Measure why the current 24-frame path misses its CPU budget, avoid rendering undeliverable frames, reuse safe per-LED cycles, and precompute only when state and program identity make it correct.

12. **Remove Usage Center I/O from the UI refresh path.** Key equivalent states by stable value or perform the merge entirely on a worker. The steady-state UI path should perform no disk or Keychain reads.

13. **Create serial, drainable persistence writers.** Consolidate history and reset-state writes while preserving deduplication, private permissions, fsync, atomic replacement, and bounded shutdown.

14. **Add priority-aware latest-wins device writes.** Coalesce obsolete animation frames while preserving asks, failures, explicit previews, and final trailing-edge state.

15. **Move hooks off the critical path without losing order.** Use a bounded ordered ingress queue or sidecar, not detached untracked processes. Define overload behavior and ensure app shutdown drains or records rejected events.

16. **Let displays sleep during keep-awake when selected.** Separate agent-process keep-awake, system sleep prevention, display wake, battery policy, and closed-lid behavior into clear choices.

17. **Add local health instrumentation.** Track render duty cycle, dropped batches, delivered FPS, queue depth, write latency, source freshness, worker count, shutdown latency, and refresh duration. Show aggregates locally and never send them to a cloud service.

18. **Deepen “Why this light?”** Include source age, chosen semantic signal, winning priority, suppressed events, active Scene, device role, DND decision, Reduce Motion substitution, and renderer timing.

19. **Create a fast change gate.** Run Ruff, imports, contracts, secret scanning, fixture validation, and a focused test slice on ordinary changes. Reserve hardware, signing, full-suite, and Instruments gates for appropriate self-hosted or manual runs.

20. **Execute packaging behavior in tests.** Smoke-test ZIP and PKG creation, certificate errors, artifact naming, version parsing, checksums, appcast output, and missing-tool behavior.

21. **Make timing tests deterministic.** Replace wall-clock sleeps with fake clocks, explicit events, bounded joins, and deterministic effect seeds.

22. **Complete the immediate accessibility repair.** Add keyboard access for lid presets, accessible names and descriptions for editors and preview controls, visible help beyond tooltips, and tested Reduce Motion parity.

23. **Run installed-app and hardware QA.** Cover clean install, upgrade, permissions, settings persistence, menu, Agent Browser, Usage Center, Alcove, Screen Bar, Pro, Dot, sleep, wake, lid, DND, low power, device removal, network failure, and recovery.

### P2: Architecture and maintainability

24. **Create an explicit application composition root.** Stop relying on import-time bootstrap and namespace injection. Keep compatibility facades temporarily, but prohibit new business behavior in the legacy controller.

25. **Extract pure decisions one boundary at a time.** Prioritize signal selection, notification arbitration, effect selection, device targeting, brightness, acknowledgements, and announcer content.

26. **Replace broad settings injection with typed feature settings.** Each subsystem reads only its owned settings and exposes a stable change contract.

27. **Separate settings, runtime cache, permissions, and detected capabilities.** Give each its own persistence, invalidation, migration, and diagnostic behavior.

28. **Define provider capability contracts.** A provider declares whether it supports lifecycle, questions, answering, usage, costs, reset forecasts, remote observation, transcript fallback, and invocation-scoped monitoring.

29. **Support multiple instances of one provider.** Personal and work accounts receive distinct consent, credentials, usage, labels, colors, retention, remote-sharing rules, and open-session actions.

30. **Create provider fixtures and ownership gates.** Use synthetic provider-owned fixtures, dated compatibility manifests, no live secrets, and an allowlist for cross-provider identifiers.

31. **Use serialized commands, receipts, and drainable workers selectively.** Apply them to ordered ingestion, persistent writes, acknowledgement, and release-critical transactions. Do not rewrite JR Bar as a fully event-sourced application.

32. **Add a signed Sparkle update channel after packaging stabilizes.** Bind appcast entries to checksums and signing receipts, support staged channels, and test upgrade and rollback behavior.

33. **Formalize adaptive refresh acceptance.** Preserve the existing CodexBar-inspired approach while proving freshness, backoff, no menu-tracking I/O, and low idle cost.

### P3: High-value product improvements

34. **Add an Alcove confidence ladder.** Distinguish fresh, stale, permission denied, disconnected, unsupported, not following, and recovering through text, geometry, and motion.

35. **Add a multi-alert announcer stack.** Show the count, highest-priority source, keyboard navigation, stable ordering, acknowledgement, and an unobtrusive collapsed state.

36. **Complete answer-in-place.** Provide provider-capability-gated replies or approve/deny controls with explicit send state, timeout, retry, cancellation, and jump-to-session fallback.

37. **Add configurable global actions.** Begin with a hotkey to reveal the current ask or control surface. Avoid globally intercepting ordinary typing and expose conflict detection.

38. **Add manual and scheduled DND.** Separate mute, dim, pause, asks-only, and fully dark behavior. Integrate Focus modes, quiet hours, temporary overrides, and a visible return time.

39. **Add a safe Clear Agents action.** Preview what will be cleared. Remove stale presentation state and acknowledgements without deleting history, credentials, hooks, or remote configuration.

40. **Add “Since you were away.”** Store content-minimized outcomes and transitions with explicit consent and retention. Never require full prompt or transcript storage.

41. **Add sticky fleet bands.** Give active projects or machines stable segments, preserve identity as states change, and collapse to a full-width shared effect only when the fleet genuinely shares one state.

42. **Add invocation-scoped “Watch this run.”** Observe one native provider invocation without permanently editing provider configuration. Restore the original process exit behavior and settings on every termination path.

43. **Extend remote observation cautiously.** Prefer authenticated, bounded event streaming over remote command execution. Keep message text, usage, and capacity behind separate consent.

44. **Add a self-contained demo and sandbox.** Simulate agents, asks, errors, completions, quota, devices, remote machines, weather, DND, low power, and notification-light behavior without installing hooks or reading credentials.

45. **Add Scenes.** Focus, Calm, Night, Demo, Travel, and DND coordinate semantic policies, surface roles, brightness, motion level, notification behavior, Reduce Motion, and device selection.

46. **Add a minimal phone glance after local security is proven.** Make it read-only by default, authenticated, content-minimized, and usable over the user’s private network without opening a general remote-command channel.

47. **Add integrations only behind a safe local API.** Stream Deck, Waybar, scripts, and automation clients use a versioned authenticated or redacted contract with explicit capabilities.

48. **Support versioned data-only effect and Scene packs.** Validate schema, migration, safety, accessibility, and preview before import. Never run arbitrary code from a pack.

### P4: Unified ambient effects and notification system

49. **Create the unified effect registry.** Make one authority for identifiers, descriptions, semantics, surfaces, parameters, safety, energy, Reduce Motion, versioning, and compilation. Existing effects migrate without changing current defaults.

50. **Turn Studio into the Effect Studio and Preview Lab.** Add the gallery, side-by-side surface simulation, synthetic event timeline, physical preview guard, comparison, assignment scopes, import and export, and “Why this effect?” inspector described above.

51. **Create semantic event-to-effect routing.** Users assign effects to meaning, not implementation details. The router arbitrates asking, failure, notifications, work, completion, environment, and idle according to priority and courtesy policy.

52. **Add Glance Light, the classic notification LED reimagined for JR Bar.** It is optional, low-energy, persistent when appropriate, and available on Dot, a reserved Pro endpoint, a Screen Bar endpoint orb, and an optional menu-bar accent.

   Default Glance Light language:

   - Unanswered question: two soft pulses in the provider or project identity, repeating at a calm interval until resolved or acknowledged
   - Failure needing intervention: three deliberate pulses plus a positional failure signature; color is supplementary
   - Completed but unseen: one short wink every 30 seconds for 15 minutes, cleared when the outcome is opened or acknowledged
   - Informational notification: steady dim marker for five minutes
   - Several notifications: display the highest priority, then summarize the count in the announcer and menu
   - DND: fully dark by default, with an optional dim asks-only marker
   - Low power or serious thermal state: static marker or greatly reduced cadence

   Glance Light stores notification identity, priority, created time, expiration, acknowledgement, privacy class, and destination. It never contains prompt text. Opening or acknowledging a notification clears it across all surfaces.

53. **Enhance every existing motion as a semantic, configurable primitive.** Keep current choices and defaults while adding consistent parameters, descriptions, previews, accessibility substitutes, energy classification, and surface adaptation:

   - **Automatic:** show the current mapping and why it was selected; allow a Scene-specific map without hiding urgent overrides.
   - **Breathe:** phase-align surfaces; expose calm amplitude and duration; clamp to a static luminous base in Reduce Motion.
   - **Duotone:** let the two tones represent identity and state; validate luminance contrast and color-vision separation.
   - **Chase:** expose direction, spacing, and softness; use direction to imply flow only when the event has direction.
   - **Gradient:** support bounded palette endpoints and smooth state morphing without unnecessary full-frame work.
   - **Heartbeat:** distinguish decorative provider rhythm from the reserved asking signature; expose rest length while retaining safe cadence.
   - **Scanner:** expose beam width and trail; adapt to a narrow flare on shared segments.
   - **Knight Rider:** retain the wide overlapping eye as a distinct mechanical style; cap speed and provide a non-sweeping substitute.
   - **Comet:** expose head width, trail length, direction, and finite-pass mode for transitions.
   - **Flicker:** use deterministic seeded variation, bounded luminance, and a static warm substitute under Reduce Motion.
   - **Stack:** expose fill direction and release behavior; map queue or milestone counts only when the data is reliable.
   - **Twinkle:** expose sparse density and seed; prevent rapid random flashes and bright clustering.
   - **Drift:** preserve slow detuned ambience; reduce sampling cadence and offer a steady substitute.
   - **Converge:** support endpoints-to-center and source-to-destination variants; use it for merge or handoff semantics.
   - **Aurora:** use a small validated palette and slow layered waves; classify it as an ambient, higher-energy effect.
   - **Tide:** expose fill range and cycle length; use it for cyclical quota, timer, or capacity meaning.
   - **Marquee:** expose spacing, direction, palette rotation, and finite passes; avoid endless high-speed rotation by default.
   - **Steady:** make it the lowest-energy persistent state and the default Reduce Motion substitute.
   - **Blink:** support named safe cadence patterns rather than arbitrary frequency; never exceed the existing flash threshold.

54. **Add effect history, acknowledgement, and explainability.** The Agent Browser records content-free effect events such as “Codex asked, Glance Light shown, acknowledged from Screen Bar.” Users can inspect what appeared, why, where, and whether it was suppressed, without storing prompt text.

55. **Split firmware programs into finite introductions and optional loops.** Let a transition play once, then settle into a steady or ambient loop without replaying the introduction.

56. **Add Firefly Completion.** Freeze the completing session’s segment for the two-second burst, play a localized traveling sparkle, then release back to live assignment. This avoids identity jumping mid-celebration.

57. **Add Handoff Baton.** When one agent completes and another begins within a bounded window, travel once from the source segment to the destination segment. Do not infer handoff when only timing coincidence exists unless project or task evidence supports it.

58. **Add Recovered Grace Note.** A previously failed source that becomes healthy plays one restrained recovery wipe, then returns to its normal state. Deduplicate repeated reconnect churn.

59. **Add Ask Heartbeat Sync.** Multiple simultaneous asks share one synchronized safe cadence rather than competing flashes. Screen Bar text and the alert stack identify the actual sessions.

60. **Add Turn-Length Ember.** Deepen saturation in broad, disclosed duration buckets. Avoid minute-by-minute writes and never imply that elapsed time proves progress or difficulty.

61. **Add Completion Meniscus.** Play one liquid center ripple in the Alcove or Screen Bar when a completion becomes visible. Reduce Motion substitutes a brief static highlight.

62. **Add Rainstick Idle.** Move one dim pixel at a low frequency to say “alive and watching.” It is opt-in, night-aware, and disabled during low power, DND, sleep, or any higher-priority signal.

63. **Add Dot Binary Heartbeat.** Use one LED for highest-priority state and one for fleet-size band or unseen-notification presence. Provide a documented legend and keep asking and failure distinguishable without color.

64. **Add Milestone Odometer.** Play finite steps at user-defined completion counts. It is opt-in and based on trustworthy completed outcomes, not raw hook volume.

65. **Add Fleet Arrival and Departure.** A trusted remote machine joining or leaving produces one quiet endpoint wink. Connection flapping is debounced and does not create repeated alerts.

66. **Create semantic courtesy signatures.** Completion, recovery, handoff, interruption, failure, quota reset, calendar, reminder, battery, weather, and notification each receive a distinct finite geometry and cadence. Do not grow the picker with effects that differ only cosmetically.

### P5: Open-source health and ecosystem

67. **Publish a JR Bar roadmap and adaptation ledger.** Use Now, Next, and Later sections. Classify upstream and fork ideas as adopted, adapted, surpassed, rejected, or waiting on evidence.

68. **Publish provider-adapter and effect-authoring guides.** Document fixtures, privacy, capabilities, safety grammar, topology, preview scenarios, energy budgets, accessibility, and compatibility rules.

69. **Add focused issue templates.** Include provider drift, hardware, effect proposal, accessibility, privacy, performance, release, remote peer, and integration reports.

70. **Add community standards.** Publish a code of conduct, support boundaries, vulnerability reporting path, compatibility policy, and lightweight governance.

71. **Create a data-only community gallery.** Require schema validation, deterministic preview, safety compilation, license metadata, accessibility behavior, and no executable code.

72. **Refresh upstream and fork research on a bounded cadence.** Review high-signal PRs and forks without accepting an open-ended merge obligation. Every adaptation must fit JR Bar’s local-first, privacy, performance, and semantic-light rules.

## Data flow

1. A provider, system integration, device, or user action produces a bounded event.
2. The canonical state layer establishes source identity, freshness, privacy class, and acknowledgement state.
3. The semantic router derives zero or more candidate signals.
4. Priority, DND, Focus, Scene, courtesy, Reduce Motion, thermal, low-power, and device-role policies select the visible signal for each surface.
5. The effect registry resolves a validated effect and parameters.
6. The surface renderer compiles the effect through existing safety and firmware boundaries.
7. Preview output remains ephemeral. Committed output is written only after an explicit user action or canonical state transition.
8. Content-free diagnostics record the reason, selected effect, surface, duration, and acknowledgement.

## Failure and recovery behavior

- Unknown or invalid effect identifiers fall back to the safest semantic default, usually Steady.
- Imported packs fail closed and remain uninstalled when schema, parser, accessibility, or safety validation fails.
- A failed physical preview releases the device and keeps committed output unchanged.
- A disconnected device does not block Screen Bar or menu operation.
- Queue overload preserves asks, failures, acknowledgements, and final trailing-edge state while dropping obsolete animation frames.
- Sleep, app termination, display changes, hardware removal, and Reduce Motion changes release preview sessions and recompile active output.
- Stale provider data changes the confidence presentation. It does not continue pretending to be live.
- Notification acknowledgement is idempotent and synchronizes across surfaces.

## Acceptance gates

### Effect-level gate

- Plain-language purpose and trigger documented
- Deterministic preview fixture
- Pro, Dot, and Screen Bar adaptation defined where supported
- Reduce Motion substitute defined
- Color-vision and luminance review passed
- Safety compiler and real firmware parser passed
- Flash, duration, interrupt, and courtesy budgets passed
- Energy class and target cadence recorded
- Cancellation, sleep, and hardware-removal behavior tested

### Product gate

- No prompt or transcript storage required for ambient behavior
- No new production dependency without owner approval
- Ruff, type or import checks, relevant tests, and canonical gates pass
- Installed UI verified with keyboard and accessibility inspection
- Exact candidate passes signed packaging and notarization evidence
- Instruments trace meets the encoded menu, CPU, energy, and main-thread budgets
- Physical Pro and Dot behavior observed, not inferred only from unit tests

## Scope decomposition for implementation planning

This roadmap is too large for one implementation branch. After owner review, implementation planning should divide it into independently gated programs:

1. Release and security truth
2. Runtime performance and persistence
3. Architecture seams and provider contracts
4. Unified effect registry and semantic router
5. Effect Studio and preview experience
6. Glance Light and notification acknowledgement
7. Existing-motion enhancement and accessibility
8. New semantic effects and firmware intro-loop support
9. Product interactions, fleet, mobile, and integrations
10. Open-source documentation and community surface

No program begins until its own implementation plan names files, tests, installed-app evidence, migration behavior, and rollback boundaries.
