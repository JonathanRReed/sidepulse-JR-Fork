# 2026-08-26 full feature audit — ledger

Five audit lanes (signals/alerts, settings wiring, LED/devices, Screen
Bar, usage/providers) swept every feature against its wiring. This file
records what was FIXED in the 0.4.0 audit waves and what was found but
deliberately deferred, so the findings outlive the session that made
them. Verdicts and file:line evidence live in the session transcripts;
the deferred list below is the actionable remainder.

## Fixed in 0.4.0 (see CHANGELOG for user-facing wording)

Reconnect truth model + failure gates; reset confetti + jump detector +
edge-baseline race; quota_alerts un-neutered + switch; connection-loss
detect/render; sessions chart from hook ledgers; percent-mode
persistence + provider round-trip + gap rendering; Screen Bar sampler
alpha + asleep show() work; firmware-reboot STATUS.TXT path; timebox
chime audible; stage-3 webhook decoupled from display tier; Studio
text persistence (end-edit + capture); Usage pane status line
(glance_summary); Devices pane hot-plug rebuild under category nav;
cursor stored-token fallback; antigravity/openai-api action branches;
devin/antigravity pace windows; trickle precedence + pinned-display
respect; marquee freeze-snap; night-dim popup sync; stale-controls
re-sync batch (full-screen switch, keep-awake-on-battery, menu-bar
label, escalation URL, webhook boxes); charging-pulse write-churn
bucketing; graph worker placeholder/pending/QoS/memo + first tests.

## Deferred — ranked

1. **Cross-Mac usage sync is dead in the app** (CLI-only): the UI seam
   `project_usage_center(state, merged_sync=…)` is called without the
   parameter. Also the CHANGELOG's "encrypted" claim is wrong (HMAC-
   signed plaintext) and replay has no freshness check.
2. **Statuspage incidents never reach snapshots** — menu rows work, but
   `ProviderUsageSnapshot.incident` is None at every construction site,
   so the Usage Center's incident line is dead.
3. **Quota-runway LED display** is selectable but `quota_runway_state()`
   returns None unconditionally; the forecast pane and
   capacity_forecast/calibration modules are runtime-dead behind it.
4. **Cost coverage**: MODEL_PRICING lacks fable/claude-5 rates (55% of
   this machine's tokens unpriced, dollars silently undercounted) and
   codex cost is skipped by code choice though records carry the data.
   Needs a real pricing decision, not a guess.
5. **Alcove bracket renderer regressions** (screen_bar_runtime replaced
   the in-view renderer): band no longer reaches the capsule corners
   (EDGE_INSET 8 + MAX_BAND_WIDTH 420 clamp), no longer lifts to kiss a
   short capsule, and the corner-reach test asserts against dead code.
   Also: quota ember unreachable in classic mode on notchless displays;
   "bracket" style silently changes colors too; `hide()` clears
   `_display_asleep` (narrow re-sleep hole); stale 5.0 default
   band_height vs patched 6.0 constant; screen_bar_design semantic
   policy is dead code.
6. **Claude legacy plane**: `claude_access_token()` passes
   allow_prompt=True from a background worker (violates the
   credentials.py invariant; consent-ledger cooldowns mitigate), and
   the legacy/JR planes still double-fetch the usage endpoint with
   different credentials when plan-limits is enabled.
7. **Claude terminal-gate fingerprint** watches a file most installs
   don't have (keychain-only) — gate lifts only on the hourly cap or a
   manual reconnect. Acceptable-by-design today; a keychain fingerprint
   (metadata query, no prompt) would close it properly.
8. **Devin org-header 401** can delete a valid token on Reconnect when
   organization_id is missing (re-import usually restores it).
9. **Snooze scope**: snoozed families still show in the Agent Browser
   shelf and still light LEDs/notify — the menu is the only surface
   that honors it. "SidePulse will retry" on failed preference saves is
   false (dirty flags written, never read).
10. **Studio housekeeping**: the LED-program Studio lives on the "Lid
    Animations" page while "Studio" names the color studio; library
    "Burn as Power-Up" breaks with 2+ devices (editor button does it
    right); active-lid variants are preset-only (no editor/duration/
    reset — and `default_lid_animation` raises for those kinds, so wire
    carefully); byte-count message reports last device only; dedupe not
    voided on bare device_path reassignment.
11. **Settings leftovers**: focus_profile/focus_signal popups,
    timebox on/off fields, signal speed/intensity sliders, usage range
    popup + hook field never re-synced; display-mode popup and
    min-glow / resting-glow sliders unregistered; dead dials
    (closed_lid_system_override_enabled, local_activity_history_enabled,
    forecast_release_authority); hidden knobs (quota thresholds dial,
    alert_burst, screen_bar_wing_length); tips are one-way off.
12. **Dead code inventory**: signals.quota_resets (zero callers),
    interruption_policy.plan_deliveries, `sidepulse serve` (works,
    orphaned), LID_ANIMATION_CHOICES, mailbox v1 migration,
    agent_browser handle_key_command, `sidepulse usage configure
    --reset-celebrations` note (now meaningful again), doctor lacks an
    eject-guard check, field-diagnostics doubled "0".
13. **Auto-hide menu bar users** lose the Screen Bar entirely with no
    diagnostic (space_hides_menu_bar conflates auto-hide with
    full-screen).
14. **Motions are dead in whole multi-agent layouts** beyond the
    documented wave-degrade: Cycle never consults agent_motion; Spatial
    Split honors only Steady; Relay collapses everything to the baton;
    aurora==drift byte-identical in shared strips. The Agents-pane copy
    names only four motions as simplifying. Needs a degrade-policy
    decision, then `_cycle_program`/`_segment_for_agent` work.
15. **Previews collapse 15 of 18 motions into 2 shapes** — thumbnails,
    hover try-outs, and the hardware preview push all route through the
    4-bucket PROVIDER_ANIMATION_STYLES bridge while the live solo
    render plays the real shape. What you preview is not what you get;
    the fix is routing previews through compose_presentation_program.
16. **Solo live render ignores the classic Idle/Working style pickers
    and gentleness sliders** (hardcoded floor 0.05 / peak 1.0): one
    working agent renders ~2× brighter than the same agent in a crowd,
    and those dials are no-ops whenever exactly one agent is engaged.
    Product decision needed: should the solo compositor honor
    fade_range/animation_style?
17. **Urgent states lose all motion at fade ceiling 100%** (urgent
    floor is lifted to the ceiling; at 1.0 floor==peak, so Ask/Failed
    render steady). Either cap the slider below 1.0 for urgent lift or
    keep a minimum swing.
18. **Screen Bar min-glow floor silently overrides its brightness
    slider below 25%** (values 1–63 all render as 64; explicit 0
    works). Surface the floor in the slider UI or compose differently.
19. **FEATURE-MATRIX.md is stale** (2026-08-20): no 18-motion
    vocabulary, charging trickle, night warmth/dim, lid animations,
    timer display, Studio v1. Also stale in-code comments:
    status_bar_legacy "keeps its choice" claim about the withheld
    runway display, and the CLI still accepting `--display
    quota_runway` while silently storing agent.
