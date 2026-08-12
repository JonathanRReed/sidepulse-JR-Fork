# SidePulse performance fix list — final ranking (judge pass)

All anchors below re-verified by reading current source at `/Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork`. Line numbers are **current** (several drifted from the submitted findings; corrected here).

## Dropped / merged before ranking

- **DROPPED — `AgentMonitor._latest_statuses()` full replay** (`collector.py:200-238`). Confirmed real, but verified reachable only from `cli.py:713/728/758` (`cmd_watch`/`cmd_leds`). The GUI's `LiveAgentMonitor` never calls `snapshot()`/`_latest_statuses()`, and the transcript-fallback `AgentMonitor` only calls `iter_records()`/`input_signature()`. Zero user-felt impact for the menu-bar app. Park it.
- **MERGED** — "release_preview_engines frees only 28 of ~98" + "leaks `color_preview_wasm`" → one fix (#6). Same function, same commit.
- **MERGED** — "scan_usage totals.records unbounded" + "scan_usage materializes every historical record" → these are the **same defect** (`usage_stats.py:368`). One fix (#12).
- **MERGED** — "WASM step JSON round-trip" folded into the `led_wasm.py` refactor (#7); same file, same call path, one test pass.
- **NOT duplicative of the done-list** (checked each): the change-gated `setNeedsDisplay_` already shipped does *not* cover #4/#5 (the gate fires "changed" on essentially every frame during WORKING/ASK/IDLE — verified `virtual_led_colors` at `virtual_device.py:369-395` is time-varying for all but DONE). "Preview WASM engines released on window close" shipped only a partial release (#6 is the gap). "Settings pane-gating of provider probes + signal cards" gated *refresh*, not *construction* (#5) and not the rest of `refresh_settings_window` (#15).

## Gating reality check (drives the ranking)

Verified defaults in `settings.py`: `notification_blinks_enabled=True` (218), `focus_sync_enabled=False` (300), `calendar_alerts_enabled=False` (229), `codex_transcripts_enabled=False` / `claude_transcripts_enabled=False` (175-176). The lid timer at `status_bar.py:533-539` is scheduled **unconditionally** — no setting gates it.

---

# Ordered fix list

### 1. `pollLid_`: get the `ioreg` fork/exec off the main thread
**Impact/risk: highest.** Only always-on, ungated, per-second main-thread process spawn in the app. Affects 100% of users, 100% of the time.

- Anchors: `status_bar.py:5830-5850` (`pollLid_`), timer at `status_bar.py:533-539` with `LID_POLL_SECONDS=1.0` (`lid_sleep.py:30`), `lid_sleep.py:57-69` (`read_lid_closed` → `subprocess.run(["/usr/sbin/ioreg",...], capture_output=True, text=True, timeout=2)`).
- Spec (minimal): add `self.lid_poll_in_flight = False` beside the other watcher flags. In `pollLid_`, return early if in-flight; otherwise set it, and run `read_lid_closed()` inside `threading.Thread(target=..., daemon=True).start()`, posting `{"ok": bool, "closed": ..., "error": str}` back via `self.performSelectorOnMainThread_withObject_waitUntilDone_("lidChecked:", payload, False)`. Move the entire existing body from `if closed is None:` onward (lines 5839-5850+, including `last_lid_error` / `last_lid_closed` state) into a new `@objc.IBAction def lidChecked_(self, payload)`; clear `lid_poll_in_flight` there **unconditionally on entry** (the `weatherChecked_` bug class — `pollWeather_`'s own comment at `status_bar.py:777-780` documents why the worker must always post).
- Copy the pattern verbatim from `pollWeather_`/`weatherChecked_` (`status_bar.py:756-800`). Do **not** attempt the native IOKit rewrite in this pass — it is a strictly larger change with a real behavior-parity risk; ship the thread hop first.

### 2. `_colors_for_draw()` runs twice per rendered frame
**Impact/risk: best ratio in the list.** ~10-line change, pixel-identical, halves per-frame color math for the main bar *and* every live thumbnail simultaneously.

- Anchors: `virtual_device.py:660-687` (`_colors_for_draw`, unconditionally stamps `self._smoothed_at = time.monotonic()` at line 672), `virtual_device.py:1270-1285` (`redraw_`'s change-gate calls `view._colors_for_draw()` purely to build the quantized hash), consumers at `727-735` (`drawRect_`), `875` (`_draw_wings_only`), `1056` (`_draw_compact_accent`).
- Spec: in `redraw_`, after computing the gate colors, stash them on the view: `view._frame_colors_cache = (self._frame_serial, colors)`. Have `_colors_for_draw()` return the cached list when `self._frame_colors_cache[0] == current serial` without touching `_smoothed_at`/`_smoothed_colors`. Simplest correct form: give `_colors_for_draw` an internal `_colors_for_draw_cached()` that the three draw sites call, while `redraw_`'s gate keeps calling the advancing version — one filter advance per frame, one snapshot reused by the paint.
- Guard: the "snap on first frame after a pause" branch (`(now - last_time) > 0.5`, line 676-682) must stay driven by the *gate* call only, or a hidden-then-shown bar will slew.

### 3. `pollNotifications_`: off the main thread, persistent read-only connection
**Default-on.** Blocks the main run loop every 2s on `sqlite3.connect` + JOIN, with a hard 0.5s worst-case stall if `usernoted` holds the DB.

- Anchors: `status_bar.py:678-724` (`pollNotifications_`), `NOTIFICATION_WATCH_SECONDS=2.0` (`status_bar.py:305`), timer at `560-567`; `notification_watch.py:41-49` (`_connect`, fresh `sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.5)` per call), `51-63` (`latest_record_id`), `65-90` (`delivered_after`) — both `connection.close()` in `finally`.
- Spec, two independent parts (land both):
  1. **Thread hop.** `self.notification_poll_in_flight` guard; run the `latest_record_id()` / `delivered_after(cursor)` call on a daemon thread; hop `{"ok", "cursor", "identifiers", "error"}` back via `performSelectorOnMainThread_withObject_waitUntilDone_("notificationsChecked:", payload, False)`. Everything from `self.notification_record_cursor = cursor` (line 705) down — the color match, `signal_hold_seconds`, `refresh_(None)`, the one-shot revert `NSTimer` — moves into `notificationsChecked_` on the main thread. Keep the existing `NotificationWatchUnavailableError` → `notification_watch_retry_at = now + NOTIFICATION_WATCH_RETRY_SECONDS` backoff, just applied in the main-thread handler.
  2. **Connection reuse.** Add a module-level `_connection: sqlite3.Connection | None` in `notification_watch.py` opened with `check_same_thread=False`, reused across calls, closed+reset to `None` on any `sqlite3.Error`. This is safe *only after* part 1 confines the calls to one worker thread — do them in that order.

### 4. Screen Bar base draw: ~440 bridged Quartz fills per frame, ~13k/sec, continuously during WORKING
**Biggest sustained CPU cost in the app; medium risk, so staged.**

- Anchors: `virtual_device.py:948-997` (`_fill_glow_row`, exactly 4 `fill_rect_with_cg` calls per `BLEND_COLUMN_WIDTH=2.0` column — bloom/soft/core/hotline at lines 966, 975, 984, 991), `502-509` (`fill_rect_with_cg` = `CGContextSetRGBFillColor` + `CGContextFillRect`), `727-760+` (`drawRect_`), `999-1054` (`_draw_wing_riser`, up to 2 fills × 48 steps × 2 risers), `WINDOW_WIDTH=220.0` (30), `FRAME_RATE=30.0` (106). Wing length clamp `0-400pt` confirmed in `settings.py`.
- Spec — **stage A only in this pass** (all pixel-identical, no visual review needed):
  - Hoist the four `tone_mapped_led_color(...)` calls out of the inner loop where their arguments are loop-invariant, and hoist `LED_BAND_HEIGHT + glow_height * 0.45` / `glow_height * 0.55` / the `1.15` hotline rect out of the `while`.
  - **Run-coalescing:** quantize each column's `(red, green, blue, alpha)` to the same 1/1024 precision `redraw_` already uses (`virtual_device.py:1277`), and merge *adjacent* columns whose quantized tuple is identical into one wider rect per layer. Contiguous same-color rects fill identically — this is a provable no-op visually, and it collapses the flat/dark regions (which the existing `max(...) <= 0.001` skip at line 959 already hints are common) and the entire wing taper into a handful of fills.
  - Cache the per-frame column list (`column_x`, `column_width`) keyed on `(x_start, x_end, notch_width, wing_offset)` — the geometry only changes on reposition/settings change, not per frame.
- **Stage B (separate change, behind a visual A/B):** pre-render the per-column ramp into a 1-D `CGImage` and `CGContextDrawImage` it. Do not bundle this with stage A — Quartz interpolation will not be pixel-identical, and this is the app's signature visual.

### 5. Build settings panes lazily instead of all 10 up front
**Biggest one-time user-felt stall: first gear click builds ~98 `JSContext`+WASM engines before the window can appear.**

- Anchors: `status_bar.py:8494` (`build_settings_window`), panes dict at `8607-8618` (all 10 built eagerly at `8597-8605`), `pane.setHidden_(key != DEFAULT_SETTINGS_PANE)` at `8629`, `DEFAULT_SETTINGS_PANE = "profile"` (`6827`), `tableViewSelectionDidChange_` (`3051-3066`) only toggles `setHidden_` and never builds. WASM-heavy panes: `_build_led_behavior_pane` (`7939`) → `make_signal_style_card` (`7715`, 70 `_mini_led_view` instances), `_build_color_studio_pane` (`8711`, 12), `_build_lid_preset_row` (`8314`, ×4 = 16). Each `setProgram_` → `virtual_device.py:649-658` `_ensure_wasm_controller` → `led_wasm.py:49-66` `JSContext.alloc().init()` + `new WebAssembly.Module(...)`.
- Spec: change `panes` from `{key: NSView}` to `{key: callable}` builders. In `build_settings_window`, build **only** `DEFAULT_SETTINGS_PANE` ("profile" — verified zero WASM thumbs) and install its constraints. In `tableViewSelectionDidChange_` (line 3059, before the `setHidden_` loop), if `self.settings_panes[selected_key]` is still a callable, call it, run the same 4-anchor constraint activation currently at `8620-8628`, merge its returned `fields`/`buttons`/`controls` into `self.settings_fields` / `settings_buttons` / `device_settings_controls`, and replace the dict entry with the built view.
- Safety already verified: `set_field_value` / `set_checkbox_state` are null-tolerant (`if field is not None:`), so `refresh_settings_window`'s unconditional sweep is safe against not-yet-built panes. `refresh_colors_window()` is already called from `tableViewSelectionDidChange_` *after* the pane work — keep that ordering so it runs against a built Color Studio pane.

### 6. `release_preview_engines()` — sweep everything, and fire it on window close
Merged findings. The function's own docstring (`status_bar.py:1112-1114`) states the intent; the implementation delivers it for 28 of ~98 engines, and only if the user happens to visit Color Studio.

- Anchors: `status_bar.py:1111-1121` (`release_preview_engines` — touches only `colors_animation_thumbs` (1115) and `lid_animation_thumbs` (1116)); untouched engine holders: `signal_color:{key}` (`7728`), `signal_thumbs:{key}` (`7761`), `signal_preview:{key}` (`7799`), plus `self.color_preview_wasm` / `self.color_preview_programs` (`425-426`, populated at `3170-3180`). Sole caller: `animate_colors_preview_once` (`3261-3263`), reached only via the 12Hz `color_preview_timer` started by `start_colors_preview_animation` (`3187`) ← `refresh_colors_window` ← `tableViewSelectionDidChange_` `if selected_key == "color_studio"` (`3062`). Grep confirms `wasm_controller = None` appears only at 1119/1121. No `windowWillClose_`/`windowShouldClose_` exists anywhere in the file.
- Spec:
  1. In `release_preview_engines`, add a sweep of `self.settings_fields` mirroring `redrawSignalPreviews_` (`1497-1507`): for `signal_preview:` / `signal_color:` prefixes set `view.wasm_controller = None`; for `signal_thumbs:` (a dict) loop `view.values()` and clear each.
  2. Add `self.color_preview_wasm = {}` **and** `self.color_preview_programs = {}` — clear both together. Verified necessary: `ensure_colors_preview_wasm`'s early return at `3170-3171` (`if self.color_preview_programs.get(led_count) == program: return self.color_preview_wasm.get(led_count)`) hands back a stale `None` and silently kills the preview if only one dict is cleared.
  3. Stop depending on the Color Studio timer. Add `windowWillClose_` on the controller, set it as the settings window's delegate in `build_settings_window`, and call `stop_colors_preview_animation()` + `release_preview_engines()` there. Keep the existing call in `animate_colors_preview_once` as a belt-and-braces path.

### 7. `led_wasm.py`: one shared `JSContext` + one compiled `WebAssembly.Module`
After #5 this still costs ~70 engines the moment the user opens the Signals pane.

- Anchors: `led_wasm.py:46-66` (`__init__`: `JSContext.alloc().init()` then `evaluateScript_(_javascript_controller(wasm_base64))`, which does `new WebAssembly.Module(b64ToBytes(...))` + `new WebAssembly.Instance(...)` per controller — grep confirms zero module- or class-level caching), `95` (`_javascript_controller`), `68/71/82` (`reset`/`parse`/`step`), `211-220` (`global.sdledStep` → `JSON.stringify(pixels)`), `82-92` (`json.loads(value.toString())`).
- Spec:
  - Module-level `_shared_context` created once, evaluating a script that compiles the `Module` **once** and exposes `sdledCreate(handle, ledCount)` / `sdledStepHandle(handle, nowMs)` / `sdledParseHandle(...)` / `sdledResetHandle(...)` over a `handles = {}` registry, each entry holding its own `WebAssembly.Instance` (+ its own `memory`/`exports`/`outputPtr`) instantiated from the shared `Module`. `SdLedWasmController` becomes a thin handle wrapper allocating a monotonic integer id; `wasm_controller = None` on a view must therefore also delete the handle — add `__del__` or an explicit `close()` called from `release_preview_engines`, or the leak in #6 returns in a new shape.
  - **In the same change**, replace `JSON.stringify(pixels)` (line 219) with returning the plain JS array and read it with `value.toArray()` instead of `json.loads(value.toString())` (line 84). Note honestly: at 24 ints per call the win is small (measure before claiming it); it is worth doing only because the file is already open.
- This is the largest single refactor on the list. It has its own tests (`led_wasm` is exercised by the parse-error path) — do not bundle it with #5.

### 8. `focus_sync_scale_factor()` — short TTL cache
Opt-in (`focus_sync_enabled` defaults False), but when on it is ~4.7 uncached `read_text()`+`json.loads()`+recursive DFS per second on the main thread, with the correct pattern sitting 2,100 lines up in the same file.

- Anchors: `focus_sync.py:51-61` (`_load_focus_json`), `81-92` (`active_focus_mode_identifiers`, zero memoization); `status_bar.py:4725-4746` (`focus_sync_scale_factor`, direct uncached call at 4738); hot path `sync_leds` (`5038`) → `sync_virtual_status_device` unconditionally at `5054` **before** the `led_animation_until_monotonic` gate at `5056` → `effective_brightness_for_device` (`5136` → `4675`) → `4700`; `refresh_` calls `sync_leds` at `901`, floored at `EVENT_REFRESH_FLOOR_SECONDS=0.25` (`317`, checked `2960`). Second and third call sites: `pollScreenBrightness_` at `631` and `640`, `BRIGHTNESS_WATCH_SECONDS=3.0` (`312`).
- Spec: add `def active_focus_ids_cached(self) -> tuple[list[str] | None, bool]` modeled byte-for-byte on `active_focus_summary`'s cache (`status_bar.py:2591-2593`, `2619`) but with a **1.0s** TTL (not 5.0 — the LED path must still feel immediate when a Focus turns on). Cache the `FocusSyncUnavailableError` outcome too, or an FDA-less machine keeps paying the `OSError` path. Route all three call sites (`4738`, `631` via `focus_sync_scale_factor`, and the direct `focus_sync.active_focus_mode_identifiers()` at `640`) through it.

### 9. `ingest_transcript_fallback()` — stop re-sorting the whole record union on the main thread
Opt-in, but when on it is an O(n log n) sort of up to 18,000 records **on the main thread** at up to 4Hz during exactly the busy moments.

- Anchors: `status_bar.py:2879-2906` — `signature = monitor.input_signature()`, then `records = sorted(monitor.iter_records(), key=lambda r: r.logged_at)` (2893) on **any** signature change, then a full re-walk at `2898-2903` discarding everything `<= self.transcript_watermark`, calling `self.monitor.ingest_record(record)` per survivor (2902). Called from `refresh_` (main thread). Caps: `CODEX_TRANSCRIPT_MAX_FILES=12` / `CLAUDE_TRANSCRIPT_MAX_FILES=24` × 500 lines (`collector.py:28-31`). Signature aggregates per-file `(mtime, size)` so it flips whenever any one of up to 36 files is touched.
- Spec: keep `self.transcript_file_signatures: dict[str, tuple[float,int]]`. Expose from `AgentMonitor` a `changed_records(previous_signatures)` that returns `(new_signatures, records_from_changed_files_only)` — the per-file record tuples from `_cached_transcript_records` (`collector.py:362-384`) are already time-ordered per file, so sort **only the concatenation of the changed files' tuples**, not the union. Feed that through the existing watermark filter unchanged. This composes with the already-shipped 45s rglob TTL and per-file mtime caches; it removes the one step those caches don't cover.

### 10. Throttle `_prune_expired` in `ingest_record`
Always-on during agent work, and it holds the exact lock the main thread's `snapshot()` needs.

- Anchors: `collector.py:434-456` (`ingest_record`, `with self.lock:` then unconditional `self._prune_expired(datetime.now(timezone.utc))` at 454), `458-481` (`_prune_expired`, full list-comp over `statuses_by_key.items()`, docstring "Callers hold self.lock"), `483-489` (`snapshot()` takes the same lock). Precedent to mirror: `maybe_write_latest_state` / `_write_latest_state` (`collector.py:519-545`), whose comment names this exact contention.
- Spec: add `self._last_prune_at = 0.0` next to `self._latest_state_written_at` (`collector.py:429-430`). In `ingest_record`, replace line 454 with `now_m = time.monotonic()` / `if now_m - self._last_prune_at >= PRUNE_INTERVAL_SECONDS: self._last_prune_at = now_m; self._prune_expired(datetime.now(timezone.utc))`. Set `PRUNE_INTERVAL_SECONDS = 60.0` — `STATUS_RETENTION_SECONDS` is a 24h horizon, so per-event precision is meaningless. Leave the `write_latest_state(force=True)` shutdown path alone.

### 11. `pollCalendar_` — async/background EventKit fetch
Opt-in and only every 30s, so real but low-frequency. The sibling module already shows the way.

- Anchors: `status_bar.py:725-755` (`pollCalendar_`, synchronous `calendar_watch.next_event_start(...)` at 736), `CALENDAR_WATCH_SECONDS=30.0` (`300`), timer at `569-576`; `calendar_watch.py:99` (`store.eventsMatchingPredicate_`); contrast `reminders_watch.py:81-113` (`fetchRemindersMatchingPredicate_completion_`, and its docstring calls out the deliberate async difference from `calendar_watch`).
- Spec: prefer the smaller change — `threading.Thread` + `performSelectorOnMainThread_withObject_waitUntilDone_("calendarChecked:", payload, False)` with a `calendar_fetch_in_flight` guard, exactly as in #1/#3. Everything from `was_active = now < self.calendar_glow_until` (740) onward moves to `calendarChecked_`. Do **not** switch to an async EventKit variant here; `eventsMatchingPredicate_` has no drop-in async twin and the reminders API shape is different.

### 12. `scan_usage`: stop materializing all-time history into `totals.records`
Worker thread (already off-main, per the done-list), so this is RSS/CPU growth rather than a stall — but it grows monotonically with the user's history forever.

- Anchors: `usage_stats.py:276` (`scan_usage`), `300-313` (unbounded `root.rglob("*.jsonl")` + `codex_root.rglob`), `328` (`all_records.extend(records)`), **`368` (`totals.records = [r for r in all_records]` — assigned *before* the `since_epoch` filter, which at `377` only gates the summed fields)**, `369-375` (a `seen: set[str]` dedupe built and then discarded), `415-448` (`daily_buckets`, rebuilds its own dedupe set at 426-430 then filters to `days`), `451-461` (`hourly_session_counts`, third full pass for today only). Callers: `status_bar.py:944` (`scan_usage`), `969` (`daily_buckets(totals.records, days=graph_days)`), `985` (`hourly_session_counts(totals.records)`); `usage_graph_days` is user-settable up to 365.
- Spec: add a `records_since_epoch: float | None = None` parameter to `scan_usage`. Move the assignment at line 368 to *after* the dedupe loop, building the list inside that loop: keep a record only if `dedupe not in seen` **and** `epoch >= records_since_epoch`. Caller passes `now - (self.settings.usage_graph_days * 86400)` from `status_bar.py:944`. Then delete the redundant dedupe in `daily_buckets` (426-430) — the list it receives is already deduped — and document that precondition on both helpers. Note the trap the finding correctly flagged: do **not** reuse the existing `since_epoch` (today's midnight, `status_bar.py:943`) for this, or the up-to-365-day chart goes blank.

### 13. Evict `_transcript_records_cache` entries that fall out of the recent-N window
Genuine unbounded growth — one entry per session transcript file ever seen, each holding up to 500 `HookEvent`s whose `.raw` retains full untruncated tool output.

- Anchors: `collector.py:134` (init), `362-384` (`_cached_transcript_records`; written only at 379; grep confirms no `.pop()`/`del` targets this dict anywhere in the file), `348-360` (`_recent_transcript_files`, sliding window of `CLAUDE_TRANSCRIPT_MAX_FILES=24` / `CODEX_TRANSCRIPT_MAX_FILES=12`), payload retention at `collector.py:899` (`"tool_response": row.get("toolUseResult") or content` — full, vs. the display-only `DETAIL_TEXT_CAP = 160` at line 44). Owner `self.transcript_monitor` (`status_bar.py:2908-2911`) is rebuilt only by `reload_monitor()`, never on a timer. Contrast the correctly-bounded `_transcript_file_list_cache` (135).
- Spec: at the end of `_recent_transcript_files` (after line 359, on the cache-miss branch that just recomputed `paths`), drop stale keys for that root's provider: `live = {str(p) for p in paths}`, then `for key in [k for k in self._transcript_records_cache if k[1] not in live and _same_root(k[1], root)]: del self._transcript_records_cache[key]`. Do the same for `_log_records_cache` (133, keyed `(provider, path, limit)`) for symmetry. Only evict against the freshly recomputed list, never the TTL-cached one, or a 45s window of churn evicts live entries.

### 14. `codex_rate_limits()` re-walks the tree `scan_usage()` just walked
Same 5-minute worker call, so low urgency — but it is a whole second `rglob` + `stat()` of every file for one piece of information already in hand.

- Anchors: `usage_stats.py:178-190` (`codex_rate_limits`: `max((p for p in root.rglob("*.jsonl") ...), key=lambda p: p.stat().st_mtime)`), vs. `usage_stats.py:305-316` (`scan_usage`'s codex walk + per-file `stat()`); called back-to-back at `status_bar.py:944-949` and `957-959`.
- Spec: have `scan_usage` record `newest_codex_path` / `newest_codex_mtime` while it is already stat'ing inside the `for path, parser in paths:` loop (313-316), expose it on `UsageTotals`, and give `codex_rate_limits` an optional `newest: Path | None = None` that short-circuits the walk. Caller passes `totals.newest_codex_path`. Behavior identical.

### 15. Pane-gate the rest of `refresh_settings_window()`
Real but modest — pure AppKit setters, no I/O. It is the natural companion commit to #5.

- Anchors: `status_bar.py:3798` (`refresh_settings_window`, ~196 lines), called unconditionally from `show_settings_window` (`2998`) and from `tableViewSelectionDidChange_` (`3066`) on every sidebar click. Only two blocks are gated today: `if current_pane == "agents" and not probes_fresh` (`3807`) and `if current_pane == "led_behavior"` (`3920`).
- Spec: wrap each remaining block in `if current_pane == "<owning pane>":`, mirroring those two exactly. Leave unconditional only: the `message` field, `settings_path`, `debug_log_status`. Take particular care with `refresh_screen_bar_preview()` — it calls `setPreviewWhiteBrightness_`/`setMinGlow_`/`setCompactMode_`/`setFrame_` and marks a `VirtualLedView` dirty even while its pane is hidden; gate it to `colors_screen_bar`. Keep the full unconditional sweep on the `show_settings_window` entry path (pass a `full=True` flag) so nothing goes stale on first open.

### 16. Cheap-thumbnail draw mode for `_mini_led_view` instances
Residual after #5 and #7: with the Signals pane open, ~70 52×20pt views each run the full 4-layer bloom/soft/core/hotline path (~26 columns × 4 = ~104 bridged fills per redraw) at 8Hz.

- Anchors: `status_bar.py:7616` (`_mini_led_view` instantiates a real `VirtualLedView`), counts verified exactly: 57 pattern thumbs + 7 previews + 6 swatches (Signals) + 12 (Color Studio) + 16 (lid presets) = 98; `redrawSignalPreviews_` (`1491-1507`) already does viewport culling but no pane check; the existing `setCompactMode_` flag (`virtual_device.py:557`) is the hook.
- Spec: add a `thumbnail_mode` flag alongside `compact_mode`, set by `_mini_led_view`. In `drawRect_` (`virtual_device.py:727-732`), branch to a single flat-fill pass — core layer only, `BLEND_COLUMN_WIDTH` × 2 — skipping bloom/soft/hotline entirely. At 52×20pt the three extra layers are visually indistinguishable; verify by screenshot diff before/after rather than by argument. Also add `if self.current_settings_pane != "led_behavior": return` at the top of `redrawSignalPreviews_` (line 1497) — the Color Studio tick already gates this way and Signals does not.

### 17. Memoize the menu-row icon composites
Ranked last deliberately: `update_status_menu` is **already** signature-gated and frozen while open (verified `status_bar.py:910-915` and its docstring), so these composites run only on genuine content change, not per event. The finding is correct; the user-felt payoff is now small.

- Anchors: `status_bar.py:9919-9944` (`composite_app_icons` — `NSImage.alloc().initWithSize_` + `lockFocus` + 2 `drawInRect_fromRect_operation_fraction_` + `unlockFocus`, no memo), `9946-9975` (`horizontal_icon_pair`, same), `9852-9860` (`session_row_icon_for_status`), call site `9753` inside `build_session_menu_item` (`9719`), invoked per row from `6172/6215/6232/6249`. Precedent: `_app_icon_cache` (`10038`, consulted `10045`, filled `10053`).
- Spec: **do not key on `id()`** — verified `image_for_symbol` (`10229`) has no cache and returns a fresh `NSImage` per call, so `id()` keys would never hit and would alias freed objects. Instead: (1) add a `dict[tuple[str,str], object]` memo to `image_for_symbol` keyed on `(symbol, description)` — icons are static, cache positives forever, mirroring `app_icon`'s own comment; then (2) memoize at the top of the chain — cache `session_row_icon_for_status` on `(status.mode, status.provider, normalized_origin_text(status.origin))`, which is the actual stable identity of the composite and skips both offscreen allocations in one hit.

---

**Suggested commit grouping:** (#1, #3, #8, #11) as one "watchers off the main thread" pass sharing the `pollWeather_` pattern and one test; (#2, #4-stage-A) as one render pass; (#5, #6, #15) as one settings pass; (#7, #16) as one WASM/thumbnail pass; (#9, #10, #13) as one collector pass; (#12, #14) as one usage pass; #17 standalone. Nothing in the list depends on a fix ranked below it except #16, which is worth far less before #5 lands.
