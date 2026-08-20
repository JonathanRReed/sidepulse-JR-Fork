# Deep-dive remediation backlog — 2026-08-19

Three parallel audits (state-machine truth, architecture, product-law
conformance) ran against `2fc1494` + the day's fixes. What was FIXED the
same night is listed at the bottom; everything else is the ranked
backlog. File/line references are as of that night.

## Fix next (correctness, small)

1. **CLI replay re-stamps old events with rebuild time** — the CLI
   `AgentMonitor` path gives yesterday's grok events `updated_at=now` on
   every refresh, so the CLI can never age anything out and its "stale"
   counts mix 1-second-old with day-old rows. Find where replayed
   statuses get their timestamps and carry the event epoch through.
2. **Non-monotonic demotion (truth D2)**: a dead WORKING row displays
   "Completed" at 10min and resurrects to "Working" at 60min
   (`status_for_snapshot`). The demotion target is also a lie of kind —
   a crashed turn is not Completed. Introduce a displayed
   "ended (unconfirmed)" distinct from provider-confirmed terminal.
3. **TOOL_RUNNING exempt from silence rules** (truth D3): extend the
   silence demotion beyond mode==WORKING, or default
   `tool_running_timeout_seconds` on (it defaults to 0 = disabled).
4. **Lease expiry re-freshens dead works** (truth D7): the
   TIMING_UNCERTAIN → FRESH flip-back on lease expiry upgrades works
   from a source silent for an hour. Cap the flip-back by the presence
   horizon.
5. **Requests can pin NEEDS_YOU past their work's life** (truth D9):
   expire LIVE requests when the owning work turns terminal; resolve
   request-id-less Stop payloads.
6. **claude PostToolUseFailure layer disagreement** (truth D12): adapter
   says lifecycle FAILED, legacy mode map says WORKING (deliberate
   2026-08-18 decision). Ratify one and align the adapter.
7. **Same-second cross-session ordering drops SessionStarts** (truth
   D11): second-granular hook timestamps + static tie-break ranks can
   classify session B's start as OLDER than session A's stop. Consider
   sub-second timestamps in hook.py.

## Product-law items needing an owner decision

- **Ask temporality** (surface V4): grant says "blink until dealt
  with"; the shipped glance render is 2 taps + static anchor. Ratify
  one; encode in `signals.py`; have presentation_policy and colors
  consume it.
- **Completion celebration while actively attended**: every turn-end
  celebrates during a live conversation (reads as flashing). T3's
  answer: celebrate only UNSEEN completions (completedAt >
  lastVisitedAt). Decide and wire into the sweep gate.
- **Screen Bar announcer words** (surface V6): rung 2 carries no text —
  session name + the actual question exist nowhere on it. This is
  Wave 2's hover pill; until then rung 2 duplicates rung 1.
- **Settings promises with no implementation** (surface V7): stage-2
  "menu bar flash" (the DEFAULT escalation tier does nothing above the
  ramp), the left wing-tip quota ember (hard-coded 0.0), provider pins
  ignored by the Screen Bar's multi-agent render, speed slider offering
  0.3s cycles the compiler clamps to 0.5s. Implement or reword each.
- **Identity color divergence** (surface V3): dropdown dots and the
  completion sweep use the hashed palette the LED side abandoned; the
  strip uses brand+lightness. Unify via the same branch colors.py uses.
- **Provider pins limited to claude/codex** (`with_device_provider_pin`
  raises for the other eight registered providers).

## Architecture investments (ranked, from the architecture audit)

1. ~~Dead-selector/orphan-callback invariant test~~ SHIPPED
   (tests/test_no_orphaned_callbacks.py; found and killed pollDevices_).
2. Declarative FeatureBinding table for the 17-timer registry.
3. Kill `settings_window._install()` namespace injection (61 invisible
   names) + freeze-list ratchet.
4. Extract menu construction (~3,200 self-free lines) → menu_projection.
5. Route the device-discovery revalidate + keepalive poker results
   through performSelectorOnMainThread (the two unlocked shared states).
6. ~~ARCHITECTURE.md truth pass~~ SHIPPED (layer-3 controller, provider
   usage constellation, write pipeline, settings stack). The
   doc-coverage ratchet test remains.
7. Capacity refresh orchestration extraction (E6, ~1,470 lines).
8. Focus/interrupt/timebox policy extraction (E2, ~400 lines).
9. Break the colors ↔ led_status cycle by moving LedDisplayState into a
   leaf module.
10. Pair each extraction with moving its tests out of the 1MB
    test_sidepulse.py.

Full extraction line-ranges for status_bar_legacy (E1-E8),
settings_window (S1-S4), and _collector_legacy (C1-C3) are in the
architecture audit transcript; the monolith split order is E1 → E2 →
E3/E4 → E5 → E6 → E7 → E8.

## Paper cuts (surface audit §3)

"Last heard" age on stale/idle rows; dead session_row_suffix ("Plan
ready") — wire or delete; two header shapes for one fact; three
provider-label tables (partially fixed: agent_browser now knows
antigravity/kiro — consolidate into PROVIDER_SPECS); wasm-failure
fallback ignores user colors; ✓ badge vs "Clear Finished (N)" derive
from different sets; Needs You shelf is recency-ordered while everything
else is static; calendar glow duration is the one courtesy signal not
budget-derived (declare the exception like weather's, or derive it).

## Fixed the same night (for context)

- Presence horizon (1h) in the shared snapshot layer + mailbox
  sectioning + menu-bar title/VoiceOver counts: a dead session can no
  longer read as present anywhere the snapshot feeds. Never shorter
  than the injected staleness window.
- Sleep-aware clock discontinuity (wall AHEAD of monotonic = nap, not
  distrust) + kern.boottime boot identity + globally-reported
  continuity elected by live sources only.
- projection_for_device unbound `pin` NameError (hardware writes
  crashed whenever a sub-agent worker existed) + regression tests.
- Timebox chime through the interrupt budget (was the one raw NSSound).
- "Setup…" ellipsis literal (the facade rename finally fires).
- Agent Browser labels for antigravity/kiro; browser memo minute-bucket
  (shelves age without needing an unrelated event).
- Orphaned-callback invariant test; pollDevices_ removed.
