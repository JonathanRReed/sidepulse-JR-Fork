## SidePulse — THE implementation plan (judged against HEAD `91006fd`, verified by reading source)

Repo: `/Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork`
All line numbers below were re-verified at HEAD, **not** taken from the story text (the stories were written against a pre-`91006fd` tree and some anchors moved ~+19 lines).

---

### 0. State of the world — three stories are already shipped, drop them

| Story | Verdict | Evidence |
|---|---|---|
| Sidebar iconography (stream 2 #1) | **SHIPPED** — drop | `91006fd`; `native_ui.sidebar_cell_view(label, symbol)` at `native_ui.py:690-732` renders a template SF Symbol; `status_bar.py:2834` passes `SIDEBAR_ICONS.get(key)` |
| Official quota APIs, Claude half (stream 3 #1) | **SHIPPED** — drop, but see finding C | `63d5f98`; `claude_quota.py` (154 lines) wired at `status_bar.py:960-968`, payload index 5, Profile switch `toggleClaudePlanLimits_` at `1878` |
| Official quota APIs, Codex half | **ALREADY SATISFIED a better way** — drop `codex_wham_usage()` entirely | `usage_stats.codex_rate_limits()` already read at `status_bar.py:942-948`, `primary.used_percent` already in the Profile codex line |
| Per-mode pulse floors (stream 1 #3) | **ALREADY SATISFIED** — no code, keep the guard test in the backlog | `colors.py:70,76-77,406-411,544-556`; `led_status._pulse_floor_color:175` has the `floor <= 0.0 → "off"` branch |

The owner's "being implemented in parallel tonight" dials are **already committed** (`cbeac9a`, 23:50). Everything in items 1–4 below is a *gap around* those dials, not a re-implementation of them.

**Dropped outright (do not build now):**
- *See every harness* — 5 of 7 named providers already ship as real hook providers (`providers.py:484-494`); the story's `~/.grok/sessions/**` file-watcher would be a **second, wrong mechanism** next to `~/.grok/hooks/sidepulse.json`. Copilot Workspace / Amp are undocumented-dialect discovery work, which the freeze forbids.
- *One dashboard for every provider* — `usage_stats` parses cost for Claude and Codex only; 5 of 7 providers have zero cost signal. Nothing to fuse yet. Sequence after the data exists.
- *LED shows budget state* — blocked on an owner decision, not on code. See §3.

---

### 1. Ranked plan — impact per unit risk

Order respects the owner's stated sequence: dimming truth (1–4) → UI feel (5–9) → fusion depth (10).

| # | Item | Risk | Why it ranks here |
|---|---|---|---|
| 1 | Dim floor: live drag **+ the preview never gets min_glow at all** | trivial | The dial he is dragging tonight has no live feedback *and* its own miniature ignores it |
| 2 | Screen Bar "Resting glow" slider is a **silent no-op** | trivial | A fake-feeling control sitting next to the real one — exactly what he rejects |
| 3 | Notch housing rim survives "pitch black" | trivial | 0% currently lies |
| 4 | Escalation visibility floor + regression test | low | Safety property; multiplication alone loses to integer rounding |
| 5 | Confirmation message → fading toast | low | 102 call sites, one function body |
| 6 | Sidebar row hover feedback | low-med | Single most Raycast-defining missing micro-interaction; zero such code exists |
| 7 | Pane crossfade | low | Panes are identically pinned; pure alpha, no reflow |
| 8 | Real empty states (Devices pane + dropdown) | trivial | Hardware-first product's first-run moment |
| 9 | Stat-forward Today card | low-med | No second rung in the type scale exists anywhere |
| 10 | Adaptive refresh cadence + rescan on menu open (+ the missing quota tests) | low | The only fusion item that fits QUALITY MODE: tightens what exists |

---

### 2. Exact specs (top 10)

#### 1 — Dim floor tracks the thumb, and the preview obeys it at all
**New finding the story missed:** `grep -rn "setMinGlow_\|set_min_glow"` returns exactly one caller — `status_bar.py:4841`, the *real* Screen Bar. The settings-pane `preview_view` (built `status_bar.py:6852-6858`, registered `6923`) is **never** given a min-glow, not at construction, not in `refresh_screen_bar_preview` (`3819-3848`). So the mini bar ignores the dial even after mouse-up.

- `status_bar.py:6817-6823` — add `continuous=True` to the `make_slider(...)` call (`native_ui.py:526` already accepts it).
- `status_bar.py:6852-6858` — after `preview_view.setHasNotch_(True)`, add `preview_view.setMinGlow_(float(target.settings.screen_bar_min_glow))`.
- `status_bar.py:3819-3848` `refresh_screen_bar_preview` — add `preview.setMinGlow_(float(self.settings.screen_bar_min_glow))` next to the existing `setPreviewWhiteBrightness_` line (3831).
- `status_bar.py:1854-1861` `setScreenBarMinGlow_` — restructure exactly like `setDeviceBrightness_` (`2027-2048`):
  ```python
  fraction = max(0.0, min(1.0, float(sender.doubleValue()) / 100.0))
  preview = self.settings_fields.get("screen_bar_preview_view")
  if preview is not None:
      preview.setMinGlow_(fraction)          # always, so the thumb is tracked
  event = NSApp.currentEvent()
  if event is not None and event.type() == NSEventTypeLeftMouseDragged:
      return                                  # skip save/sync/refresh mid-drag
  # …existing four lines unchanged…
  ```
- No changes to `settings.py`, `effective_brightness_for_device`, or `virtual_device.py`. `VirtualLedView.setMinGlow_` is `virtual_device.py:840-841`.

#### 2 — Make the Screen Bar's resting glow real (it is dead today)
Confirmed: `_set_virtual` inside `sync_virtual_status_device` (`status_bar.py:4819`, closure at ~`4858-4866`) calls only `apply_channel_gain_to_program(program, device.channel_gains)`. `apply_resting_glow_to_program` (`led_status.py:139`) is invoked only from the Agent/Battery controllers (`led_status.py:764,772`), and every controller loop filters `device.device_id != VIRTUAL_DEVICE_ID` (`status_bar.py:5146,5153,5258,5315,5522`). The Devices pane builds the slider for *every* entry (`6626-6644`); the only virtual special case in that loop is `led_count` at `6586`.

- Change the closure body to:
  ```python
  self.virtual_status_device.set_program(
      apply_channel_gain_to_program(
          apply_resting_glow_to_program(program, device.resting_glow),
          device.channel_gains,
      ),
      started_at=started_at,
  )
  ```
  Order matters and matches `AgentLedController.sync_snapshot` (`led_status.py:763-765`): ember first, calibration second.
- Also fix the clamp/UI mismatch (**conflict**, see §3): `with_device_resting_glow` clamps to `0.35` (`settings.py:658-664`) but the slider is `max_value=25.0` (`status_bar.py:6628`). Pick one — recommend raising the slider to 35 so the clamp is reachable, or lowering the clamp to 0.25 so it is honest.
- Test beside `test_device_resting_glow_round_trip` (`tests/test_sidepulse.py:9213`): set a nonzero Screen Bar `resting_glow`, drive the program through the `_set_virtual` path, assert the rendered program contains no bare `off` token.

#### 3 — The housing rim must fade to nothing at 0%
`virtual_device.py:797-807`: two `fill_rect_with_cg` calls at fixed alpha `0.18` / `0.055`, gated only on `self.has_notch`, with no reference to `min_glow`. Meanwhile `_bracket_colors` (`832`) already scales its legibility floor by `min_glow * 0.72`.

```python
if self.has_notch:
    glow = max(0.0, min(1.0, getattr(self, "min_glow", 0.25)))
    if glow > 0.0:
        fill_rect_with_cg(cg_context, ((0.0, LED_BAND_HEIGHT - 0.55), (width, 0.55)),
                          (0.0, 0.0, 0.0, 0.18 * glow))
        fill_rect_with_cg(cg_context, ((0.0, 0.0), (width, 0.45)),
                          (1.0, 1.0, 1.0, 0.055 * glow))
```
Then append one clause to the "Dim floor" `help_text` (`status_bar.py:6828-6834`) — "…the housing edge fades with it" — and add a test next to `test_bracket_floor_zero_means_pitch_black` (`tests/test_sidepulse.py:9204`).

#### 4 — Escalation must survive the darkest possible stack
`effective_brightness_for_device` (`status_bar.py:4435-4469`) is correct in *order* — boost multiplies in at `4460`, the Screen Bar floor is a `max()` at `4461-4462` — but the guarantee is multiplication-only, and `normalize_brightness` rounds to int (`led_status.py:458-461`). Worst case: `idle_dim_scale_factor` and `focus_sync_scale_factor` both bottom out at `MIN_IDLE_DIM_FRACTION = 0.05` (`settings.py:54`), giving a `0.0025` multiplier — a base of 40 yields `0.10` vs `0.115`, both rounding to `0`. The existing test (`tests/test_sidepulse.py:8709 test_ramp_boosts_effective_brightness`) uses a physical device at brightness 100 and never probes this.

- New test near `8709`: `VIRTUAL_DEVICE_ID` device, `with_screen_bar_min_glow(0.0)`, `with_device_resting_glow(id, 0.0)`, idle-dim and Focus both at `MIN_IDLE_DIM_FRACTION`, low base brightness; assert stage-≥1 result is **absolutely** visible (e.g. `>= 8`), not merely a larger float.
- If it fails, harden at `4460-4462`: add `MIN_ESCALATION_VISIBLE_BRIGHTNESS` (suggest 12) and `if boost > 1.0: scaled = max(scaled, MIN_ESCALATION_VISIBLE_BRIGHTNESS)` **before** the Screen Bar floor line, so it applies to physical devices too.

#### 5 — Toast, not a stale label
`set_settings_message` is four lines at `status_bar.py:3850-3853`; the footer label is built at `7926-7931` and registered as `"message"`. No `alphaValue`, `NSAnimationContext`, or `NSTimer`-based dismissal exists anywhere in the tree (verified by grep across all of `src/sidepulse`).

- Keep the signature and the `log_status_bar(f"settings: {message}")` line untouched — debug logs must not change.
- Store the handle on the controller (`self._settings_message_timer`), invalidate any prior timer first, fade in over ~150ms, then schedule `NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(3.5, self, "dismissSettingsMessage:", None, False)` (same idiom as `2796-2800`).
- New `dismissSettingsMessage_` fades to 0 and clears the string.
- Set `message.setAlphaValue_(1.0)` at construction (`7928`) so the label isn't invisible before the first call.
- Zero changes to the 102 call sites.

#### 6 — Hover feedback, sidebar rows only (buttons are a separate commit)
Greenfield: no `NSTrackingArea` / `mouseEntered` / `CALayer` anywhere.

- `native_ui.py`, beside `sidebar_cell_view` (`690`): add `_HoverRowView(NSView)` overriding `updateTrackingAreas` to rebuild one `NSTrackingArea` over `bounds` with `MouseEnteredAndExited | ActiveInKeyWindow` (**`ActiveInKeyWindow`, not `ActiveAlways`** — that is what makes hover clear for free when a sheet takes key), and `mouseEntered_`/`mouseExited_` toggling a layer background at a *named concrete value* (`NSColor.controlColor` at alpha 0.35), animated ~100ms via `NSAnimationContext.runAnimationGroup_`.
- Return `_HoverRowView` from `sidebar_cell_view` instead of the plain `NSView` at `697`. Selection is unaffected — the table uses `NSTableViewSelectionHighlightStyleSourceList` (`native_ui.py:461`), a separate mechanism.
- **Do not** touch `make_button` (`491`) in this commit; CALayer under AppKit bezels needs visual QA.

#### 7 — Pane crossfade
`tableViewSelectionDidChange_` (`status_bar.py:2841-2856`) is the single choke point — `select_settings_pane` (`2866-2874`) reaches it via `selectRowIndexes_byExtendingSelection_`, so one fix covers clicks *and* programmatic jumps like `show_colors_window`. All ten panes are pinned identically to `content_container` (`7969-7978`), so alpha is safe.

- Replace the `for key, pane in self.settings_panes.items(): pane.setHidden_(...)` loop (`2852-2853`) with: bump `self._pane_transition_generation`, capture it locally; incoming pane → `setHidden_(False)`, `setAlphaValue_(0.0)`, animate to 1.0; outgoing pane (`self.current_settings_pane` before reassignment at `2851`) → animate to 0.0 with a completion block that calls `setHidden_(True)` **only if** the captured generation still matches. That counter is what makes "never stutters or queues" true.

#### 8 — Empty states
- `status_bar.py:6526-6529` — replace `make_label("No devices connected yet.")` with a new `native_ui.make_empty_state(icon, headline, body, button_title, target, selector)` (compose from `make_card:410` / `make_wrapping_label:311` / `make_button:491`; pass a *resolved* `NSImage` from `image_for_symbol` at `9576`, because `native_ui` must not import `status_bar`). Wire the button to `toggleVirtualStatusDevice:` — verified real and already wired, and `status_bar_devices` (`4529`, virtual entry at `4551-4560`) genuinely produces a "Screen Bar" row when the flag flips. Physical discovery is passive, so this is the only honest CTA.
- `status_bar.py:5824` — `disabled_menu_item("No devices")` becomes two stacked disabled items mirroring the already-good `"No agents yet"` pattern at `5691`.

#### 9 — Today card leads with the number
The dollar figure is not isolable at the UI layer: `usage_summary_line` (`usage_stats.py:400-412`) pre-formats one sentence. `UsageTotals.cost_usd` is a raw float (`usage_stats.py:66`).

- `status_bar.py:986-990` — widen the `applyUsageSummary_` payload from 6 to 7, adding `totals.cost_usd if totals.sessions else None` at index 6.
- `status_bar.py:992-1020` — capture `self.usage_cost_value`.
- `native_ui.py`, near `make_label` (`304`) — add `make_stat_label(value_text, caption_text)`: vertical stack, top label `NSFont.monospacedDigitSystemFontOfSize_weight_(30.0, NSFontWeightSemibold)` (tabular figures, not a monospace face), bottom label `make_label(..., secondary=True)`.
- `status_bar.py:6430-6440` — swap `usage_label` for the stat label; keep `detail_label`, `codex_label`, graph, legend, and the plan row (`6462+`) unchanged. Update the `usage_label.setStringValue_` site at `1016-1017` accordingly.

#### 10 — Refresh cadence + the missing quota tests
`maybe_refresh_usage_summary` (`status_bar.py:911-918`) gates on a bare `< 300.0`. `menuWillOpen_` (`1034-1038`) records `menu_last_opened_at` but **never triggers a rescan** — that is the real gap. Low Power Mode does **not** exist in this codebase (zero hits for `isLowPowerModeEnabled` / `NSProcessInfo` across `src/sidepulse`); the story's claim that it's already consumed for dimming is false — the nearest thing is the user-set `low_battery_threshold_percent`, a different concept.

- `usage_stats.py` — pure `refresh_interval_seconds(menu_opened_recently, agents_active, low_power_mode, *, official_api=False)` returning the 120/300/900–1800 ladder, with a longer floor when `official_api=True` (Claude quota is a *network* call, unlike the local scan).
- `status_bar.py` — add `low_power_mode_active` via `NSProcessInfo.processInfo().isLowPowerModeEnabled()` (new import, ~3 lines); replace the `< 300.0` literal at `917`; feed `agents_active` from `snapshot.aggregate.active_count` and `menu_opened_recently` from the already-tracked `menu_last_opened_at`.
- `menuWillOpen_` (`1034`) — call `self.maybe_refresh_usage_summary()`.
- **Scope guard:** touch only the usage/quota worker. Do not touch the `STATUS_BAR_REFRESH_SECONDS` session timer (`~510-516`) — it is already fast and already has an on-open guarantee.
- **Required companion (finding C):** add `tests/test_claude_quota.py`. Commit `63d5f98`'s message says the parser is "fixture-tested" — `grep -rni "quota|oauth" tests/` returns **nothing**, and `tests/` contains only three files. `summary_line` (`claude_quota.py:147-154`) indexes `window['label']` and `window['utilization']` with no `.get()` fallback, so a schema drift is a `KeyError` on the main thread's label path. Fixture `windows_from_payload` against a captured real `/api/oauth/usage` response before trusting those names.

---

### 3. Conflicts and landmines to flag before anyone starts

1. **`MIN_IDLE_DIM_FRACTION = 0.05`** (`settings.py:54`, enforced `1233`) is a hardcoded floor that Night Mode's "true black" would need to break. **Do not lower it globally** — that silently changes today's Idle Dimming for every existing user and contradicts the Night Mode story's own "does not change existing Idle Dimming" clause. If Night Mode ships, give it its own unclamped fraction.
2. **`screen_bar_min_glow`'s floor is deliberately skipped when `scaled == 0.0`** (`status_bar.py:4461`, guard `scaled > 0.0`). That is intent — a Focus rule resolving to "off" must stay off. Any refactor of that function must preserve the guard; add a comment-anchored test if you touch it.
3. **Resting-glow clamp mismatch:** `settings.py:659` clamps to `0.35`; the slider maxes at `25.0` (`status_bar.py:6628`). The top 10 points of the domain are unreachable. Reconcile in item 2.
4. **Escalation's guarantee is multiplicative-only** and can be erased by `int(round())` (`led_status.py:458-461`). Item 4 converts it to a floor.
5. **`_bracket_colors` already scales by `min_glow * 0.72`** (`virtual_device.py:832`). Item 3 must use the same `getattr(self, "min_glow", 0.25)` attribute, not a new setting, or the rim and the bracket will disagree at the same dial position.
6. **The Screen Bar never goes through an LED controller** (`status_bar.py:5146,5153,…`). Any future "apply X at the write boundary" feature must be added to *both* `AgentLedController.sync_snapshot` **and** `sync_virtual_status_device._set_virtual`, or it silently no-ops on the most-visible surface — the exact bug in item 2. Consider extracting a single `finalize_program(program, device)` helper so this can't recur a third time.
7. **Sidebar icons use a parallel `SIDEBAR_ICONS` dict**, not 3-tuples on `SETTINGS_SIDEBAR_ITEMS` (`status_bar.py:2834`). A new pane can ship iconless. Add a one-line test asserting every non-`header:` key has an icon.
8. **Full-bleed chrome conflicts with the fixed-size window.** `build_settings_window` pins `root` width/height by *equality* (`7892-7893`) with a documented reason, and `split.topAnchor()` is flush to `root.topAnchor()` (`7934`). Turning on `FullSizeContentView` without a ~28pt sidebar top inset *will* put row one under the traffic lights. Also, `make_sidebar_background` (`native_ui.py:193`) uses `NSVisualEffectMaterialSidebar` unconditionally and never branches on `glass_available()` the way `make_glass_panel` (`143`) does — so the story's "verify on both fallback and NSGlassEffectView" is unreachable as written.
9. **Open question for the owner, blocking the quota-LED story:** the real arbiter (`active_led_display_kind_for_device`, `status_bar.py:4673-4741`) ranks `LED_DISPLAY_AGENT` *last* as the default, while `LED_DISPLAY_LOW_BATTERY` — the exact grammar a quota glow would copy — sits at rank 4 (`4701`), **above** ask. And `LedDisplayState` has no distinct "failure" color: `BLOCKED_ERROR` and `WAITING_FOR_INPUT` both map to `#FF3A00` (`led_status.py:24-28,67-78`). So "an active ask always wins" describes behavior the copied pattern does not have. He must choose: insert above `4701` (outranks ask, like low battery) or just above the implicit agent fallback (`4741`, never preempts ask). These are not equivalent; don't guess.

---

### 4. Backlog, with the reason each is *not* in the top 10

- **"Why is it dim" summary** — genuinely useful (seven multiplicative factors now exist), but the "recomputes live while dragging" clause makes it the most expensive dimming item and a new maintenance tax on every future factor. Ship it *after* 1–4, and ship the static version first: one `dimming_factors_for_device(device, *, base_override=None)` helper next to `effective_brightness_for_device` (`4435`), rendered as a secondary label, recomputed in `refresh_settings_window`. Word `Resting glow` as its own line — it does not affect the brightness scalar, and folding it in would recreate exactly the confusion item 2 fixes.
- **Night Mode** — a new feature during a declared freeze (`docs/FORK-ROADMAP.md:3-8`), overlapping an existing Idle Dimming card (`_build_led_behavior_pane`, `status_bar.py:7373`, card at `~7385-7399`). Needs the separate-fraction design in §3 item 1 before it's safe.
- **Pace deltas** — ship the Codex half only when it ships; it is the only provider with a real ceiling today. Claude's ceiling now exists via `claude_quota` windows, so this becomes viable *after* item 10's tests prove the field names.
- **Full-bleed chrome** — cosmetic, real regression surface (§3 item 8).
- **Per-mode fade-floor guard test** — pure insurance for behavior that already works; batch it with the item 3/4 test tranche.
- **Quota LED signal, new providers, unified dashboard** — blocked on §3 item 9, on undocumented dialects, and on per-provider cost data respectively.
