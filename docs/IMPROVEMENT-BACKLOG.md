# Improvement backlog — verified 2026-08-12

Produced by a 30-agent hunt/verify workflow: six lenses (perf tail,
settings UX, first-run, code health, resilience, signals/hardware)
over the whole repo, every finding then adversarially re-verified
against the current code at its cited anchors. Only survivors are
listed, ranked impact-then-effort. The two hot-path perf findings
the hunt caught in the just-landed Alcove-follow feature were fixed
the same day (change-gated sync path, measurement TTL, raw-bytes
alpha scan) and are omitted here.

## 1. Lid preset thumbnails keep a stale selection border after you pick a new one

*Impact: high · effort: small · lens: ux-settings · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

The 2pt selected border on lid preset thumbnails is applied only at construction (_build_lid_preset_row, status_bar.py:9197-9228, border set at 9223-9226 — width only, no color, so it renders in default black rather than the accent color). selectLidPresetThumb_ (status_bar.py:3673-3690) saves the preset and calls refresh_settings_window, but that method (4275-4483) syncs the lid program text editors and never touches lid_animation_thumbs borders — the OLD preset stays highlighted and the newly clicked one shows no ring. Because the settings window is built once and never rebuilt (show_settings_window, 3411-3412; setReleasedWhenClosed_(False)), the stale highlight survives closing/reopening the window and persists until app restart. Fix: add a lid-thumb pass to refresh_settings_window mirroring the intent of _apply_thumb_selection (status_bar.py:8423-8432) — but note it cannot be reused […]

## 2. refresh_colors_window syncs a control that no longer exists and skips its replacement (Animation Style thumbs)

*Impact: high · effort: small · lens: ux-settings · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

color_fields["animation_popups"] is created empty (status_bar.py:9831), stored into color_fields (9972), and never populated — make_animation_style_popup (10147) has no callers — yet refresh_colors_window loops over it (3562-3564) while the thumbnails that replaced the popups (target.colors_animation_thumbs, assigned at 9855) get no re-sync there: their selection ring is applied only at build (9848) and their programs baked only at build (9839, via _mode_animation_thumb_program which reads mode_color at 8462). setColorPreset_'s own comment (3843-3848) says presets change animation style and demands a full refresh, and apply_preset (colors.py:803-831) confirms it — but after a preset, applyPalette_ (2191-2205), or resetColorsToDefaults_ (3960-3963), the Animation Style row still rings the old style and previews the old mode colors, permanently: the settings window is built once and […]

## 3. Signals pane's 'Calendar' card is a grab-bag of five unrelated features with weather's fields orphaned at the bottom

*Impact: high · effort: small · lens: ux-settings · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

The Signals pane's card titled "Calendar" (src/sidepulse/status_bar.py:8875-8992, built in _build_led_behavior_pane) is a grab-bag of five unrelated features: calendar glow + lead time (8876-8897), Reminders glow (8899-8908), severe-weather flash (8910-8920), quota-threshold blinks + thresholds field (8922-8943), and sub-agent asks (8945-8955) — with the weather Location override lat/lon row orphaned at the very bottom (8956-8989), after the sub-agent switch and three rows away from the weather switch it configures. The comment at 8873-8874 ("a warning light, not a calendar app -- one switch, one lead time") documents the card's original single-purpose intent and is now falsified by its contents. Split into "Calendar & Reminders", "Weather" (switch + location together), and "Quota" cards, and move the sub-agent asks row adjacent to the "Needs-You Escalation" card (8996) it actually […]

## 4. Hook install feedback from the Welcome window goes to a label that doesn't exist yet

*Impact: high · effort: small · lens: first-run · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

update_hooks (status_bar.py:4642-4669) and hooksUpdated_ (:4671-4689) report progress and errors via set_settings_message (:4586-4588), which writes to settings_fields["message"] — a label built only inside build_settings_window (:9542-9552), created lazily on first Settings open (:3410-3412). On first run only the Welcome window exists (settings_fields is {} from :414, and set_field_value no-ops on None at :10399), so "Installing Claude hooks…" and especially "Claude hooks failed: …" are visible nowhere but the log. Worse, the error branch of hooksUpdated_ (:4676-4681) returns after refresh_settings_window without touching the setup window, so a failed Install click from "Connect Your Agents" produces zero visible change — the button just sits there (success at least hides the button via refresh_setup_window at :4688). Fix: also write these messages to setup_fields["message"] — the […]

## 5. Dropdown 'No agents yet' empty state is not hook-aware and offers no click path

*Impact: high · effort: small · lens: first-run · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

build_menu's empty state (status_bar.py:6880-6886; anchors drifted ~15 lines from the citation) shows two disabled rows: 'No agents yet' / 'Start Claude Code or Codex -- sessions appear here'. The collector is entirely hook-event driven, so for a user with zero provider hooks installed that instruction can never come true. A click path does exist (the always-present enabled 'Setup...' item at ~:7064 wired to openSetup:, and the Setup window auto-opens until setup_screen_completed), so this bites users who skipped first-launch setup — impact medium, not high. Fix: when no provider has hooks installed (provider_hooks_installed :10532 over HOOK_PROVIDERS), replace the second row with an enabled 'Connect your agents in Setup...' item targeting openSetup: (:2355); keep the current teaching text when at least one hook is installed. Implementation constraints: (1) do not call […]

## 6. Corrupt settings.json silently resets everything, then auto-save destroys the evidence within seconds

*Impact: high · effort: small · lens: resilience · files: src/sidepulse/settings.py, src/sidepulse/status_bar.py*

load_settings catches any parse failure and returns all-defaults with no backup and no user-visible notice (src/sidepulse/settings.py:1257-1260); the non-dict-JSON branch at 1262-1263 is a second silent-reset path with the same problem. On the next LED-sync tick, status_bar_devices(remember=True) (src/sidepulse/status_bar.py:5370, 5435-5436) calls remember_connected_devices (5470-5504), which diffs connected hardware against the now-default settings and calls save_settings (5500), atomically overwriting the corrupt-but-recoverable file with defaults — permanently losing calibration_profiles (settings.py:248), the studio_library of user-authored programs (settings.py:330), per-device settings, and colors. The save path is properly atomic (settings.py:1446-1467), and its own comment (1452-1456) names silent default-loading as the disaster to prevent — so the fix belongs on load: rename […]

## 7. LEDS.LED/INIT.LED written non-atomically — unmount or eject mid-write leaves a torn program the hardware plays

*Impact: high · effort: small · lens: resilience · files: src/sidepulse/device_writer.py, src/sidepulse/status_bar.py, src/sidepulse/led_status.py*

write_led_program does a plain target.write_text (truncate-then-write, no scratch+rename) at src/sidepulse/device_writer.py:44-46, in contrast to settings.py:1446-1467 which documents exactly why truncate-in-place is unsafe and uses unique-scratch + os.replace. An unmount, force-eject, or cable pull mid-write leaves a truncated/empty LEDS.LED on the FAT card, and the still-powered device then reads it — the codebase's own docstring says a program the firmware can't parse "makes the device strobe red" (status_bar.py:5935-5940), and "Never emit N:off" is listed under invariants "paid for in blood" in docs/ARCHITECTURE.md. Worst on the INIT.LED power-up burn (status_bar.py:1818-1820): a torn boot file persists across device boots and nothing ever verifies or repairs it. Notably, docs/ARCHITECTURE.md:25 already describes device_writer.py as owning "atomic program writes" — the doc claims a […]

## 8. Wrong default: the weather heartbeat hides agent ASKs for the entire multi-hour warning

*Impact: high · effort: small · lens: signals-hardware · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/signals.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

SIGNAL_WEATHER is a continuous full-intensity 1.4s heartbeat (src/sidepulse/signals.py:129; "weather" is in CONTINUOUS_SIGNALS at line 49, so it loops via `repeat`) and its arbiter claim (src/sidepulse/status_bar.py:5540-5543) is simply `weather_alerts_enabled and weather_alert_active` — no hold, no expiry — active for the entire NWS alert (status_bar.py:846-856). Only the Test button and the opt-in stage-3 escalation takeover outrank it, so for the full alert duration (NWS Severe/Extreme products like Severe Thunderstorm Watches, Winter Storm Warnings, and Flood Warnings routinely run 3+ hours to days) the light surfaces show nothing but the heartbeat: notifications, quota, reminders, completions, calendar, low battery, and the agent ASK rendering are all masked. An ignored ASK still reaches the default-tier stage-2 menu-bar icon flash after ~120s (settings.py:264; […]

## 9. Grow the stage-3 webhook into a per-event light bridge (the indicator escapes the device)

*Impact: high · effort: small · lens: signals-hardware · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/settings.py*

Generalize fire_escalation_webhook (status_bar.py:3179-3222) into an outbound event bus: extract the worker-thread POST into a shared post_webhook(event_dict) helper, then call it from the existing one-shot fire points — completion sweep transition (status_bar.py:2764-2839), quota sunrise (1123-1141), weather-alert onset edge (852-855), timebox zero/stop via the pop-and-fire path beside fire_timebox_off_shortcut (3017-3023), and ask-stage onset in apply_escalation (~3113). Payloads carry {event, stage/signal, agent, label, identity hex via colors_module.identity_colors_for_agents} so Home Assistant/Hue can mirror the agent color across the room — the universal-indicator thesis (docs/FORK-ROADMAP.md:152) made physical, and a natural pair with the approved Signal API to make the app bidirectional. Two scope corrections from code review: the moment-events need NO latch (they are already […]

## 10. Audit #4 stage A still live: run-coalescing + invariant hoisting in the glow draw (~440 bridged fills/frame at 30fps)

*Impact: high · effort: medium · lens: perf-tail · files: src/sidepulse/virtual_device.py*

Audit #4 stage A (docs/PERF-AUDIT.md) remains unimplemented: _fill_glow_row (src/sidepulse/virtual_device.py:1204-1253) is byte-identical to the audit-era commit and still issues 4 fill_rect_with_cg calls (688-696) plus 4 tone_mapped_led_color computations per 2pt column (BLEND_COLUMN_WIDTH, line 118), with the loop-invariant layer rects (glow_height*0.45, glow_height*0.55, the 1.15 hotline) rebuilt per iteration, and each column's glow_color_for_column -> blended_led_color_at_x (585-653) allocating a fresh totals list and tuple. The companion audit item #2 already shipped (_colors_for_draw_cached, line 850; redraw_ stash at 1574), so this is the render pass's missing half. Spec per the audit: hoist the invariant rect heights, quantize each column color to the 1/1024 precision redraw_ already uses (1575-1578), and merge adjacent columns with identical quantized colors into one rect per […]

## 11. Audit #5 still live: first gear click still builds all 10 settings panes and ~98 WASM engines eagerly

*Impact: high · effort: medium · lens: perf-tail · files: src/sidepulse/status_bar.py, src/sidepulse/virtual_device.py, src/sidepulse/led_wasm.py*

Audit #5 confirmed still live at refreshed anchors. build_settings_window (src/sidepulse/status_bar.py:9425) constructs every pane up front (9505-9526, hidden-toggle at 9537) and tableViewSelectionDidChange_ (status_bar.py:3475-3522) only toggles hidden with a crossfade — it never builds. Verified engine count is exactly 98: make_signal_style_card (8509) x7 SIGNAL_STYLE_CARDS (8366) = 57 pattern thumbs (3 CONTINUOUS_SIGNALS x 7 offered patterns + 4 x 9) + 6 current-color swatches (show_color on 6 of 7 cards) + 7 previews = 70; _build_color_studio_pane (9620) adds 12 animation thumbs (3 ANIMATION_MODE_KEYS x 4 ANIMATION_STYLE_CHOICES, 9834-9848); _build_lid_preset_row (9198) x4 kinds x4 presets = 16. Each _mini_led_view (8409) receives setProgram_ at build time -> _ensure_wasm_controller (virtual_device.py:839) -> fresh JSContext + evaluateScript, and the immediate .parse() triggers new […]

## 12. Power pane 'Show battery on LEDs' silently stops working once a device is remembered

*Impact: high · effort: medium · lens: ux-settings · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/settings.py*

The Power pane's "Show battery on LEDs" switch (status_bar.py:8319-8321, action 2436 -> set_battery_led_display 4708-4722) writes only the GLOBAL led_display via with_led_display (settings.py:362-365). But rendering is per-device: status_bar_devices sets display=settings.display_for_device(device_id) (status_bar.py:5383), the arbiter's battery claim fires only on device.display == LED_DISPLAY_BATTERY (5600-5605), and sync_leds_now recomputes the kind per device (6247-6250). display_for_device (settings.py:367-371) prefers any per-device entry — and every connected device gets one automatically: each LED sync calls status_bar_devices() with remember=True (5435-5436, 6229), and remember_connected_devices (5470-5504) -> with_remembered_device (settings.py:562-574) persists an entry snapshotting the then-current global into led_display. So the switch works at most once (before a device is […]

## 13. Welcome window FDA row: wrong pointer, no reveal-in-Finder, wrong-binary risk, and stale status

*Impact: high · effort: medium · lens: first-run · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

The Setup window's Full Disk Access row (status_bar.py:7390-7404) says "Details in Settings > LED Behavior" — but the sidebar label for led_behavior is "Signals" (:7522), and the actual FDA walkthrough (click +, path display, remove-and-re-add instructions, reveal-in-Finder button) lives in the Focus pane's Per-Focus Rules card (:8684-8754). The Focus pane's own docstring (:8597-8599) records that this content was moved out of Signals because users couldn't find it — the Setup row's pointer is a leftover from before that move. Cross-references are circular: active_focus_summary says "see Setup" (:2858, shown in the dropdown and atop the Focus pane) and the Focus Dimming switch says "granted in the Setup window" (:8618), while Setup points back at a mislabeled Settings pane. The Setup row's "Grant…" button (:1373-1376) only opens the Privacy pane, where SidePulse won't be listed; the […]

## 14. status_bar.py monolith: 3,929 lines are already module-level functions with clean extraction seams

*Impact: high · effort: medium · lens: code-health · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/tests/test_sidepulse.py*

status_bar.py is 11,227 lines; only ~6,350 belong to StatusBarController (:396-6743). The rest are module-level functions taking target/snapshot as explicit parameters, so extraction is mostly import-shuffling (file has `from __future__ import annotations`, so `target: StatusBarController` annotations need only TYPE_CHECKING imports — no runtime circularity). Natural cuts (anchors verified at current HEAD): (1) settings panes _build_profile_pane :7667 through _build_debug_pane :9402, build_settings_window :9424, _build_color_studio_pane :9619-9977 (~2,300 lines -> settings_window.py); (2) widget/control factories add_color_swatch :10018 through checkbox_is_on :10457 (~450 lines) — per the locked fork decision that native_ui.py "becomes a genuinely first-party-grade design system" (docs/FORK-ROADMAP.md:146-148), fold these into native_ui.py rather than a new ui_controls.py; (3) menu […]

## 15. No single-instance guard; a second instance steals events.sock and quitting it permanently deafens the survivor

*Impact: high · effort: medium · lens: resilience · files: src/sidepulse/status_bar.py, src/sidepulse/ipc.py, src/sidepulse/settings.py*

main() has no lock/guard (src/sidepulse/status_bar.py:11221-11223), and `sidepulse status-bar --foreground` (src/sidepulse/cli.py:773-776) launches directly with no check for the KeepAlive LaunchAgent, giving two live instances — a mode the code explicitly anticipates (quit_ comment, status_bar.py:2662-2666) but never guards. HookEventServer.start() unconditionally unlinks the existing socket and rebinds (src/sidepulse/ipc.py:66-72), so the first instance is deafened the moment the second starts; stop() unconditionally unlinks the path (ipc.py:90-95) and runs on quit via applicationWillTerminate_ (status_bar.py:2680, 2703), so when the second instance exits nothing is bound and send_hook_event fails silently forever (ipc.py:45-46). The survivor never recovers: start_event_server is only called at launch (status_bar.py:529), hook-log replay is startup-only (status_bar.py:530, 3405-3408), […]

## 16. 'Set Up' happily completes with zero agents connected

*Impact: medium · effort: small · lens: first-run · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

run_first_launch_setup (status_bar.py:4193-4236) only processes the three Mac-level checkboxes; the provider rows (7358-7368) are separate per-row Install buttons. A first-run user who presses the highlighted default "Set Up" button (key equivalent Return, 7416-7417) without touching a provider row gets "Nothing to install." or the Mac-install messages, complete_first_launch_setup (4261-4273) marks setup complete and closes the window — leaving agent monitoring, the core feature, entirely unconfigured with only the non-clickable menu empty state as a hint. Fix: before calling complete_first_launch_setup from the Set Up path, probe HOOK_PROVIDERS via provider_spec(p).detector(None); if no provider has hooks installed, keep the window open once with a one-line message in the existing setup message field ("No agents connected yet — sessions won't appear until you install a hook above") and […]

## 17. Dead code: 14 functions with zero callers anywhere in the repo

*Impact: medium · effort: small · lens: code-health · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/collector.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/battery.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/lid_sleep.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/keep_awake.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/native_ui.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/install.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/app_bundle.py*

Dead code confirmed: 13 functions with zero callers repo-wide (status_bar.py anchors drifted +30 lines since the scan). Tier the deletions by fork policy. TIER 1 — fork-added, safe to delete now: status_bar.py make_animation_style_popup (:10146); native_ui.py stretch_to_stack_width (:659); app_bundle.py bundle_executable_path (:87). TIER 2 — dead in both fork AND upstream/main, but present upstream, so deletion trades against the locked decision that 'the Python core stays pull-compatible with upstream/main' (docs/FORK-ROADMAP.md:143-145); delete only with that merge cost consciously accepted: collector.py iter_codex_transcript_records (:580) and iter_claude_transcript_records (:788); battery.py write_battery_to_leds (:386); lid_sleep.py run_privileged_pmset_disablesleep (:129, a self-documented 'compatibility wrapper for the old name'); keep_awake.py read_status_file (:206); install.py […]

## 18. Duplicated device-setting mutation plumbing: five near-identical ~35-line methods

*Impact: medium · effort: small · lens: code-health · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

set_device_display (status_bar.py:4724), set_device_brightness (:4760), set_device_auto_brightness (:4797), set_device_channel_gain (:4834), and set_device_channel_gains_reset (:4879) repeat the same ~35-40 line skeleton: guard falsy device_id, find the device via next(... status_bar_devices(remember=False) ...), try settings.with_X(...) + save_settings, on exception set_settings_message + self.settings = load_settings() + return, then reset_led_controllers_for_device, set_settings_message, refresh_settings_window, and re-sync LEDs. They have already diverged: set_device_display resyncs via refresh_(None) while the other four use an identical last_snapshot-guarded 3-arg sync_leds call; only set_device_channel_gain has the mid-calibration branch (:4867) that re-lights the test color via _send_calibration_test; and unlike set_transcript_monitoring (:4691) and set_battery_led_display […]

## 19. Firmware-grammar validation fails open, gating the highest-stakes write (INIT.LED burn) on a validator that may be broken

*Impact: medium · effort: small · lens: resilience · files: src/sidepulse/status_bar.py, src/sidepulse/led_wasm.py*

validate_studio_program (src/sidepulse/status_bar.py:5935-5949) returns None (= valid) on ANY exception. The docstring documents this fallback as deliberate graceful degradation ("or when the parser is unavailable; size checks still apply"), which is fine for save/preview — but applyStudioAsPowerUp_ (status_bar.py:1791-1833) uses the same verdict to burn the program into every connected device's INIT.LED, which replays at every hardware boot, and its docstring plus docs/FORK-ROADMAP.md promise real firmware-grammar validation. In the fallback that promise is silently false: exactly when JavaScriptCore/wasm is broken, a firmware-unparseable (red-strobing) program within size limits can be burned with a success message. The infrastructure to fix this already exists: led_wasm.py raises LedWasmUnavailableError for missing JavaScriptCore or failed wasm instantiation, and the test suite […]

## 20. 54 controller attributes materialize via getattr instead of init defaults; drift has already started

*Impact: medium · effort: medium · lens: code-health · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/virtual_device.py*

StatusBarController.init (status_bar.py:397-524) initializes 87 attributes, but a cross-check of all 97 getattr(self, ...) sites shows 53 more attributes that are never set in init and only spring into existence inside handlers: studio_editor (5 sites), current_settings_pane (4), timebox_ends_at (3), colors_animation_thumbs (3), and ~11 more with 2 sites each (cleared_session_ids, status_menu_open, signal_preview_timer, test_signal_key, working_since, peek_until, completion_sweep_until/color, timebox_overtime_since, lid_animation_thumbs, studio_library_popup), plus ~38 single-site ones. The predicted failure mode has already materialized twice: (1) current_settings_pane defaults to "" at :3155 but None at :3483, :3662, :4279 (harmless today only because both fail the == "color_studio" test); (2) lid_animation_thumbs defaults to {} at :1174 and :3669 but uses a None sentinel for lazy […]

## 21. Per-device signal routing: 'this device only speaks asks'

*Impact: medium · effort: medium · lens: signals-hardware · files: /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/settings.py, /Users/jonathanreed/Documents/Codex/2026-08-10/hey-so-i-just-got-access/sidepulse-JR-Fork/src/sidepulse/status_bar.py*

Per-device courtesy-signal muting: add muted_signals: tuple[str, ...] = () to DeviceDisplaySetting (settings.py:150), thread it through StatusBarDevice in status_bar_devices() (status_bar.py:5380-5416) since the arbiter receives StatusBarDevice, and skip muted keys in the claims loop of active_led_display_kind_for_device (status_bar.py:5525-5643). The mutable set must be exactly the courtesy/routine signals — notification, quota, reminders, completion, all_clear, calendar, peek, battery preview, timer — never test, escalation, weather, or low battery, matching the documented invariants (low_power_active: "every device at once"; courtesy_signals_held: "hard asks and weather still break through"). This is the per-device analogue of the shipped per-Focus signal policy, so consider reusing its all/asks_only/silent vocabulary in the Devices-pane UI instead of a raw checklist. Serialization […]
