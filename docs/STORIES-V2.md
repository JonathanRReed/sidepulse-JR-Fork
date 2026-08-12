# SidePulse — USER STORIES v2: the clever round

Verified against this working tree at HEAD `94e9e73` (the repo received commits while this was written — line numbers below were re-checked at that commit; anchor by symbol name first). Nothing below repeats ULTRA-PLAN's ten items or its backlog; several stories deliberately *resolve* its recorded landmines instead.

Ranked by owner-felt impact. **Top 5 carry exact implementation sketches.**

---

## 1. Click the bar to answer ★ TOP 5

**Story:** As a developer with an agent blocked on me, I want to click the glowing notch bar itself and land in the asking session's terminal, so that the surface that *told* me an agent needs me is also the fastest way to *answer* it — no dropdown hunting, no window juggling.

**Acceptance criterion:** With an ask active, one click anywhere on the lit Screen Bar focuses the oldest unanswered ask's terminal/IDE within 1s (same open action as the dropdown row). With no ask active, the window ignores mouse events exactly as today — menu-bar clicks, notch-adjacent clicks, and Mission Control are untouched. The cursor becomes a pointing hand only while the bar is clickable.

**Feasibility:** The window is built with `setIgnoresMouseEvents_(True)` (`virtual_device.py:1237`) — the flag is per-window and cheap to flip. `VirtualLedView` (`virtual_device.py:522`) currently overrides no mouse methods, so `mouseDown_` is greenfield. The open action already exists and is pure-ish (`session_actions.session_open_target`, imported at `status_bar.py:200`, driven by `openSessionPrimary_` at `1109`). No SwiftUI, no new permissions.

**Implementation sketch:**
- `virtual_device.py` — `VirtualStatusDevice`: add `set_click_handler(handler | None)` storing the handler and calling `self.window.setIgnoresMouseEvents_(handler is None)` (guard `window is None`; also apply in `_build_window`, `:1227`). Plumb the handler onto the view. In `VirtualLedView`, add `mouseDown_(event)` → call the stored handler; add `resetCursorRects` with `NSCursor.pointingHandCursor()` gated on handler presence.
- `status_bar.py` — in `sync_virtual_status_device` (`:4943`), after the existing `set_min_glow`/`set_bracket_style` block: compute `asks = ask_statuses(snapshot)` (defined beside `unseen_completions`, `:9693`); if non-empty, `set_click_handler` to a closure that opens the *oldest* ask (reuse the episode ordering `track_ask_blocked` (`:2359`) already computes) via the same code path as `openSessionPrimary_` (`:1109`); else `set_click_handler(None)`.
- Pure seam for tests: a controller method `screen_bar_click_status()` returning the target `AgentStatus | None`; test through `isolate_controller()` per the testing rules in `docs/ARCHITECTURE.md`.
- Safety: never clickable in wings-only/compact Alcove modes' *dead* regions — the handler is whole-window, but since it is only armed while an ask exists, a mis-click's worst case is focusing the terminal you were being asked to visit anyway.

---

## 2. Quota runway is a Display, not a fight ★ TOP 5

**Story:** As a heavy Claude+Codex user, I want to set a device's Display to "Quota runway" so the strip renders my *remaining* headroom as a fill that drains toward the reset, so that budget state is ambient and continuous — and I stop doing mental math from threshold blinks.

**Acceptance criterion:** Setting any device's Display popup to "Quota runway" renders `100 − worst-window used_percent` as a left-anchored fill in that provider's brand color, updating with each usage refresh; the existing threshold-crossing blink (`e8a8515`) still fires on top of it; with no quota data yet, the device falls back to Agent display (no dark bar, no error).

**Feasibility — and why this unblocks ULTRA-PLAN §3 item 9:** Round 1 recorded "LED shows budget state" as *blocked on an owner decision* about where a quota **signal** ranks against asks. A per-device **Display** choice sidesteps the precedence question entirely: like Battery/Timer/Studio, it sits at the bottom of the claims ladder (`active_led_display_kind_for_device`, `status_bar.py:4789`) and asks/escalation/completion all outrank it by construction. No decision needed; nothing preempts an ask.

**Implementation sketch:**
- `settings.py` — add `LED_DISPLAY_QUOTA_RUNWAY = "quota_runway"` beside `LED_DISPLAY_STUDIO` (`:19`) and into `LED_DISPLAY_CHOICES` (`:20`); `with_device_display` (`:324`) validates against that tuple already.
- `status_bar.py` — arbiter: one claim beside the Studio claim in the `:4789` ladder — `device.display == LED_DISPLAY_QUOTA_RUNWAY`. Renderer: one row in `signal_display_entries` (`:5176`); the factory reads `self.quota_last_percents` (kept fresh by `track_quota_thresholds`, `:1046`, fed from usage payload index 6 at `:1043`), picks the max-used window, and returns `None` when empty — the documented factory-None → Agent fallback. Brand color mapping precedent is literally in `track_quota_thresholds` (`:1060`).
- `led_status.py` — `quota_runway_program(fraction, *, led_count, brightness, color)` next to `timer_fill_program` (`:476`), reusing its `#000000`-never-`off` indexed-fill body (the paid-for-in-blood firmware invariant) minus the trickle animation; a static fill is the honest shape for a slow-moving number.
- Devices pane: add `("Quota runway", LED_DISPLAY_QUOTA_RUNWAY)` to the popup choices (`:6835`) and a label in `device_display_label` (`:9259`) plus the popup-sync dict (`:4103`-area).
- Test: fixture percents → program contains the expected lit/dark split at 8 and 2 LEDs; empty percents → arbiter returns `LED_DISPLAY_AGENT`.

---

## 3. The Exhale ★ TOP 5

**Story:** As someone running agents all day, I want the bar to take one slow, soft breath when the *last* working session completes with nothing left needing me, so that "you're free" is a felt moment — the ambient equivalent of closing the laptop — instead of the light just… stopping.

**Acceptance criterion:** With 2+ sessions live, one finishing plays only today's identity-color sweep. When the *final* active session completes and no asks remain, the sweep plays, then the bar takes exactly one ~3s dim warm-white breath and settles to rest — never repeating, never firing on restarts/replays (same freshness discipline as the sweep), suppressed by Quiet Hour.

**Feasibility:** Smallest story on this list — everything it needs exists. Completion transitions are already edge-detected with a 2-minute freshness window in `track_completions` (`status_bar.py:2411`); the claims ladder and program-factory table are one-row extensible by design; a non-repeating DSL program is trivially inside the 512B/20-line firmware budget and, without `repeat`, the firmware holds the final dark state on its own.

**Implementation sketch:**
- `status_bar.py` — in `track_completions`, after `finished` is computed (`:2441-2452`): if every `current_modes` value is `COMPLETED` and `ask_statuses(self.last_snapshot)` is empty, set `self.all_clear_until = self.completion_sweep_until + 3.6` (sweep first, exhale second).
- New constant `LED_DISPLAY_ALL_CLEAR` beside `LED_DISPLAY_COMPLETION` (`:286`); arbiter claim directly *below* the completion claim (`:4848`-area): active when the sweep window has passed, `now < all_clear_until`, and `not self.quiet_active()`.
- `signal_display_entries` (`:5176`): factory returning the one-shot program `off 400ms cosine\n#F5EDE0 3200ms pulse` through `apply_brightness` — a single pulse from dark, back to dark, no `repeat`. Label: `"{device.name} All clear"`.
- Test beside the completion-sweep tests: finishing one of two agents never sets `all_clear_until`; finishing the last one does; a replayed (stale-age) completion sets neither.

---

## 4. Point at the notch and it answers ★ TOP 5

**Story:** As a power user, I want to rest my pointer on the notch bar for half a beat and get a 4-second "vitals" readout — Claude runway filling the left half, Codex the right, my timebox if one is running — so that the notch becomes something I can *ask*, not just watch, without opening a single menu.

**Acceptance criterion:** Dwelling the pointer over the Screen Bar's frame for ≥0.5s plays the peek program on all surfaces for 4s (moving away ends it early); it requires zero new permissions (no Accessibility, no event tap); it never fires from a pointer merely crossing the bar; idle CPU cost is unmeasurable (a 5Hz timer reading `NSEvent.mouseLocation()`).

**Feasibility:** `NSEvent.mouseLocation()` is a class method that needs no permissions — this is not a keyboard/event tap. The "timed preview claim" pattern already exists twice: `battery_preview_until` (claim in the `:4789` ladder) and `test_signal_until` (`:1560`). An `NSTrackingArea` can't work here — the window deliberately ignores mouse events (`virtual_device.py:1237`) and must keep doing so (story 1 arms it only during asks) — which is exactly why the poll design is right.

**Implementation sketch:**
- `status_bar.py` — a 0.2s `NSTimer` (started/stopped with the Screen Bar in `sync_virtual_status_device` / the disable path) polling `NSEvent.mouseLocation()` against `self.virtual_status_device.window.frame()` (guard `window is None`); 3 consecutive hits → `self.peek_until = time.monotonic() + 4.0` + `refresh_(None)`; a miss while peeking → clear early. Suppress while the status menu is open (`self.status_menu_open`, set at `:1088`).
- New constant `LED_DISPLAY_PEEK` beside `:284`'s `LED_DISPLAY_TEST`; arbiter claim just *above* the battery claim (it's an explicit user query, like the Test button, but must not outrank asks — insert below `LED_DISPLAY_COMPLETION`).
- Program: pure helper `peek_program(percents, timer_fraction, led_count)` beside `escalation_takeover_program` (`:2650`) — indexed halves built like `timer_fill_program` (`led_status.py:476`), `#000000` for unlit, brand hues from the `track_quota_thresholds` mapping (`:1060`). Timebox active → render the timer fill instead (people peek to check the clock).
- Test: pure `peek_program` snapshots at 8/2 LEDs; controller test that `peek_until` never sets while the menu is open.

---

## 5. Boot wearing your colors: the INIT.LED signature ★ TOP 5

**Story:** As a SidePulse Pro owner, I want a "Set as Power-Up Look" button in the Studio that burns my program into the device's INIT.LED, so that my hardware boots wearing *my* light — on a fresh Mac, on a friend's laptop, on an iPhone with the Dot — before any software has even launched. The device stops being a peripheral and becomes mine.

**Acceptance criterion:** Clicking the button validates the Studio program through the real firmware grammar (wasm parse, line/column errors surfaced in the settings message) and writes it to `INIT.LED` on every connected physical device; the firmware confirms by playing it immediately (documented behavior, `LEDS_FORMAT.md:33-35`); a "Restore Startup Fill" writes back a bundled equivalent of the stock one-second fill; the Screen Bar is correctly excluded (it has no INIT.LED).

**Feasibility:** Almost embarrassingly cheap: `device_writer.write_led_program` already takes `file_name` (`device_writer.py:30-46`) — the entire hardware side is `write_led_program(program, device_path=device.root, file_name="INIT.LED")`, and `StatusBarDevice.root` exists (`status_bar.py:258`). Validation must go through `validate_studio_program` (`:5138`, real `SdLedWasmController.parse`) per the ARCHITECTURE invariant that `validate_led_text` is only a size check. This is the most hardware-first story in the set and no other product can copy it.

**Implementation sketch:**
- `status_bar.py` — `_add_studio_card` (`:7952`): third button "Set as Power-Up Look" → new IBAction `applyStudioAsPowerUp_`: read `self.studio_editor` text → `validate_studio_program` (`:5138`); on error, `set_settings_message` (`:3966`) with the line/col message (same pattern as `:1550-1554`); on success, loop `status_bar_devices()` (`:4645`) for connected non-virtual devices and `write_led_program(..., file_name="INIT.LED")`; report `set_settings_message(f"Power-up look written to {n} device(s).")` — matched-count honesty, same contract as calibration Apply.
- "Restore Startup Fill": a small bundled default program constant; same write path.
- Test: dry-run write targets `INIT.LED` not `LEDS.LED`; invalid program writes nothing and surfaces the parse error; virtual device excluded.

---

## 6. Overtime is honest: the timebox turns ember

**Story:** As a timebox user, I want the final minute to shift the draining fill to amber and — when it hits zero — the bar to hold a dim ember that slowly deepens toward red until I stop it, so that blowing through my own deadline is something I *see* out of the corner of my eye, not a silent nothing after the drain ends.

**Acceptance criterion:** At T-60s the fill color crossfades to amber; at zero a chime plays once and the ember claim begins; the dropdown's stop row reads "Stop (overtime 7m)"; stopping clears instantly; an agent ask still outranks the ember (claims ladder unchanged).

**Feasibility:** `timer_fill_fraction` (`status_bar.py:2544`) and the `LED_DISPLAY_TIMER` entry (`:5266`) already parameterize color; the timer claim (`device.display == LED_DISPLAY_TIMER or self.timebox_active()`) just needs `timebox_active` widened to an "ended-but-not-stopped" state (`timebox_ends_at` kept, a `timebox_overtime` flag). Chime: same `NSSound.soundNamed_` idiom as escalation (`:2587` body). Ember program is a 2-line pulse well inside DSL limits. The FORK-ROADMAP promised "chime at zero"; nothing in the tree plays one today — this closes that quietly.

---

## 7. A shelf of looks: Studio library + Capture

**Story:** As a Studio tinkerer, I want to save named programs and — the clever half — a "Capture What's Playing" button that snapshots the exact program currently rendering on the bar into the editor, so that any live moment I love (a completion sweep, the relay chase mid-flight) becomes an editable, replayable artifact instead of something I try to reconstruct from memory.

**Acceptance criterion:** Capture fills the editor with a program byte-identical to `VirtualLedView.current_program` at click time; Save requires a passing wasm parse and a name; saved looks appear in a dropdown Studio submenu and play (as the existing Studio display claim) until stopped; the library round-trips through settings restart.

**Feasibility:** The bar literally holds the playing source text — `VirtualLedView.current_program` (`virtual_device.py:530`, updated at `:591`) — so Capture is a read, not a renderer-inverse. Library storage mirrors `calibration_profiles` (`settings.py:735-771` slot pattern); playing reuses the `LED_DISPLAY_STUDIO` claim (`status_bar.py` `:4789` ladder + `:5278` entry) with a transient "active library program" override. Note honestly: the captured text includes any `brightness N` prefix baked by `apply_brightness` — strip the first `brightness` line on capture so the look re-renders correctly under the user's live brightness.

---

## 8. Circadian white point — and the finalize_program extraction it rides on

**Story:** As someone whose LEDs face them at midnight, I want the whole light system to warm its white point after sunset (Night Shift for the strip), so that late-night SidePulse feels like candlelight instead of a shop floor — and I want the plumbing done as the single `finalize_program()` write-boundary helper the architecture docs say this codebase is one bug away from needing a third time.

**Acceptance criterion:** After the configured evening hour (or solar sunset when weather's IP-geo lat/lon is available — never CoreLocation, per the invariant), physical devices and the Screen Bar shift identically warm; toggling the feature off restores byte-identical programs; a new `finalize_program(program, device)` is the *only* place resting glow, channel gains, and warmth compose, called from both `AgentLedController.sync_snapshot` (`led_status.py:764-765, 772-773`) and the Screen Bar's `_set_virtual` (`status_bar.py:4983`), with a unit test asserting the two surfaces agree.

**Feasibility:** Warmth is exactly a channel-gain triple — `apply_channel_gain_to_program` (`led_status.py:155`) already does the math; compose warmth gains with calibration gains multiplicatively. ULTRA-PLAN §3 item 6 documents the "apply X at the write boundary in two places or it silently no-ops" landmine and suggests this exact extraction; this story pays for the refactor with a feature the owner will *see* every night. Solar math is 20 lines of pure Python (NOAA approximation) — no new deps, no new permissions.

---

## 9. Deep-work patina

**Story:** As a long-session runner, I want a working agent's breathing to slow and deepen the longer it works uninterrupted — skittish when fresh, oceanic after an hour — so that "how long has this been going" is legible from rhythm alone, the way you read a sleeping person's breathing, without a single number on screen.

**Acceptance criterion:** A fresh working session animates at its configured cycle speed; after 45 continuous working minutes the cycle is measurably slower (e.g. 1.6s → 2.6s, eased, capped); any ask/idle/completion resets the patina; escalation's speed *quickening* always wins over patina's slowing (asks must never read calmer).

**Feasibility:** The exact seam exists and is proven: `agent_render_colors` (`status_bar.py:2657`) already returns a `ColorSettings` with escalation-quickened `cycle_speed` via `with_cycle_speed` — patina is a second, opposite-signed adjustment in the same function, driven by `working_since_by_agent` (`:2401-2409`), which already tracks per-agent working starts for the dropdown's elapsed labels. Pure-function testable with a fake clock. Zero new state, zero new UI.

---

## 10. Timeboxes with intent: the Focus handshake

**Story:** As a ritual-driven worker, I want each timebox preset to optionally flip on a Focus mode when it starts and off when it ends (25 min → Work Focus via a named Shortcut), so that one click on the dropdown buys me the full ritual — draining light, silenced notifications, per-Focus dimming — and the end of the drain gives it all back.

**Acceptance criterion:** With "25 → Work" mapped, `Start 25 Minutes` runs the paired Shortcut within 2s and the dropdown's Focus line (already rendered by `active_focus_summary`) reflects the active Focus; timer end or Stop runs the "off" Shortcut exactly once (idempotent flag, no double-fire on refresh); unmapped presets behave exactly as today.

**Feasibility:** macOS's `shortcuts run <name>` CLI is invocable via `subprocess` off the main thread (the codebase already runs worker threads with the "always post your completion payload" invariant). Hooks: `startTimebox_` (`status_bar.py:2559`), `stopTimebox_` (`:2567`), plus the zero-crossing detected for story 6. Mapping persists as a small dict mirroring `with_focus_profile_rule` (`settings.py:796`). Honest caveat: Shortcuts pops a one-time per-shortcut permission toast — surface that in the card's help text, and it lives outside the sealed-bundle TCC surface (no Info.plist change, no FDA risk).

---

## 11. Quota sunrise

**Story:** As a rate-limited power user, I want a single green sunrise sweep when a quota window *resets*, so that the moment I'm rich again — the exact moment to re-queue the big refactor — announces itself, instead of me discovering spare capacity an hour late.

**Acceptance criterion:** When a tracked window's percent falls from ≥50% to near zero (reset signature) or `resets_at` passes, one brand-colored upward sweep plays once per window per reset; first observations and app restarts never fire (the T3 transition rule, same as the existing crossing-up blink); Quiet Hour suppresses it.

**Feasibility:** The mirror image of shipped code: `quota_crossings` (`signals.py:186`) fires only upward; add a `quota_resets(previous, current)` pure sibling detecting the large downward transition, called from `track_quota_thresholds` (`status_bar.py:1046`) where `previous`/`current` are already in hand. Rendering reuses `SIGNAL_COMPLETION`'s sweep pattern with the brand color through the one renderer (`style_to_program`, `led_status.py:529`). ~40 lines plus tests.

---

## 12. Per-Focus signal gates

**Story:** As a Focus-mode user, I want each Focus to choose which signals may break through — Sleep: severe weather only; Work: asks and completions, but no calendar/reminder glows; Personal: everything — so that the bar's *vocabulary* follows my context the way its brightness already does.

**Acceptance criterion:** With Work active and calendar gated, an imminent event claims nothing (and the Focus pane's "Right Now" card says so: "Work — calendar glow held"); severe weather and hard asks always break through regardless of gates; the per-Focus matrix persists and round-trips.

**Feasibility:** Both halves exist as patterns: per-Focus persisted rules (`with_focus_dim_rule`, `settings.py:903`) and per-signal gating in the arbiter — every routine claim in `active_led_display_kind_for_device` (`status_bar.py:4789`) already consults `self.quiet_active()`; add a parallel `self.focus_gated(signal_key)` beside it, fed by `focus_sync.active_focus_mode_identifiers()` (already cached 5s in `active_focus_summary`, `:2475`). UI is a checkbox matrix in the existing Focus pane (`_build_focus_pane`, `:7420`), swatch-row style — no NSColorWell anywhere near it.

---

## 13. Project hue families

**Story:** As someone running Claude *and* Codex on the same repo (plus a third session elsewhere), I want sessions to be colored by *project* — one hue family per repo, providers distinguished by lightness within it — so that the bar answers the question I actually have ("which project needs me?"), not the one it currently answers ("which harness?").

**Acceptance criterion:** Two Claude sessions and one Codex session in repo A render in one hue family, visibly distinct from repo B's family; the dropdown identity dots match the LEDs; a lone session anywhere keeps its brand color (today's rule); toggling "Color by project" off restores today's per-session identity exactly.

**Feasibility:** Identity assignment is one pure function — `identity_colors_for_agents` (`colors.py:379`) keyed by sorted `agent_id`s — and the grouping key is already collected: `origin.py` detects per-session origin (`detect_agent_origin`, `:39`; labels via `origin_label_from_payload`, `:250`) and the dropdown prints it (`menu_origin_label`). Add `identity_colors_for_agents(ids, groups=...)` deriving family hue from the group and lightness steps within (the OKLCH helpers at `colors.py:1630` make the lightness ramp perceptually even and gamut-safe). Feasibility caveat to verify first: origin must be populated for transcript-fallback sessions too, or ungrouped sessions fall back to per-session hues (acceptable degradation).

---

## 14. Wing tips as standing gauges

**Story:** As a Screen Bar devotee, I want the outermost two columns of each wing to act as persistent micro-gauges — left tip: quota ember whose brightness tracks my worst window; right tip: the unseen-done green that today only lives on the menu-bar icon — so that standing state survives whatever animation owns the center of the bar. Peripheral vision gets its own pixels.

**Acceptance criterion:** At 80% quota the left tip holds a faint amber ember through agent animations, sweeps, and timers; the right tip glows green only while `unseen_completions` is non-empty and extinguishes the instant the menu opens (the existing visit-clears rule); off by default; works in full-bracket and Alcove wings-only modes; physical devices are untouched (honest: this is a Screen-Bar-only luxury — 2 of 8 LEDs is too expensive on hardware).

**Feasibility:** The Screen Bar composes its own pixels — `_fill_glow_row` (`virtual_device.py:938`) and `_draw_wings_only` (`:865`) already treat wings specially, and `_bracket_colors` (`:809`) shows the "overlay a computed color at draw time" pattern. Add `setStandingGauges_(left_level, right_on)` on `VirtualLedView`, fed from `sync_virtual_status_device` (`status_bar.py:4943`) using `quota_last_percents` and `unseen_completions` (`:9693`). Pure drawing; no DSL involvement, so no firmware constraints apply. Costs careful visual QA in all three Alcove modes — that's the real price.

---

## 15. Escalation's stage 3 leaves the desk

**Story:** As someone who walks away, I want stage-3 escalation to optionally POST once to a webhook URL (ntfy topic, Home Assistant, a Shortcut — or the APNs relay for the iOS SidePulse push inbox that already lives in this repo at `ios/SidePulse`, which can flash a Dot plugged into my iPhone), so that "an agent has been blocked on you for four minutes" can find me in the kitchen.

**Acceptance criterion:** Reaching stage 3 fires exactly one POST per ask episode (JSON: provider, session label, ask age, count) from a worker thread; answering the ask resets the episode (existing behavior); network failure logs quietly and never retries into spam; the URL field lives on the escalation card and an empty field means feature-off.

**Feasibility:** The single-fire seam exists: `apply_escalation` (`status_bar.py:2587`) already latches `escalation_chimed` per episode — the POST latches identically beside it. `urllib.request` in a daemon thread obeying the "worker must always post its completion payload" invariant; settings field via one `with_*` mutator. The iOS path needs no app changes — `PushPayload.swift` already accepts raw LED pushes — only the user's own relay credentials; the webhook half is fully self-contained and ships first.

---

## 16. The Dot is Codex's: per-device session pinning

**Story:** As an owner of both a Pro (in the SD slot) and a Dot (on the hub), I want to pin the Dot to one provider or session — "the Dot is Codex" — while the Pro keeps the aggregate, so that two devices become two channels instead of two copies, and a glance tells me which *harness* is working without decoding blend modes.

**Acceptance criterion:** A pinned Dot renders only its provider's sessions (resting-dark when none are live — honest absence, not stale state); the Pro's aggregate is unchanged; an ask from the pinned session still escalates on *every* surface (asks are never partitionable); unpinning restores aggregate behavior byte-identically.

**Feasibility:** The per-device settings machinery is proven three times over (`with_device_display` `settings.py:324`, `with_device_blend_mode` `:727`, per-device resting glow `:675`); the pin is one more per-device field plus a status filter where `sync_leds_now` (`status_bar.py` `:5285`-area) hands `statuses` to each device's controller. The ask-escalation carve-out must be explicit in the arbiter, not the filter — escalation claims fire before the agent display is computed (`:4789` ladder), so it comes free, but a regression test should pin that down. Medium effort; highest value the day a second device is in daily use.

---

### Ranking rationale, briefly

1–5 were chosen for daily felt frequency × novelty × fit to the locked fork vision ("hardware first-class, universal indicator, first-party polish"): **1** and **4** make the bar bidirectional for the first time; **2** converts round 1's only deadlocked story into shippable depth; **3** is the highest delight-per-line on the list; **5** is the most *ownable* — no software-only competitor can boot your colors from silicon. 6–9 deepen shipped features (Timer, Studio, rendering craft) per "depth not breadth"; 10–12 extend proven pure-function seams; 13–16 are the biggest bets, ordered by how much verification or hardware context they still need.
