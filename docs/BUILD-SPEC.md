# JR-BAR — the build spec (round two)

From source-first study of CodexBar (c4ed34d0) and t3code (96bfa67b),
the SidePulse community forks, Alcove, and macOS menu-bar practice.
Accessed 2026-08-14.

## The menu-bar item

## A1. The menu-bar item

**Verdict: stop setting a title string, start compositing one fixed-size NSImage.** Today `set_status` (status_bar.py:5899-5966) mutates `button.setTitle_()` between `""`, `" (2)"`, `" ✓"`, `" Waiting (3)"` under `NSVariableStatusItemLength` (created at status_bar.py:1724-1730). That is the jitter source and it fights Bartender/Ice, which key off item identity and position.

### The canvas (this is the whole anti-jitter answer)
One template `NSImage`, **always exactly 26 × 18 pt**, redrawn on every refresh. Width never changes, ever, regardless of state, count, or capacity. Zones inside the fixed canvas:

| x-range | contents |
|---|---|
| 0–18 | state glyph (SF Symbol, 18pt, drawn into the image) |
| 18–20 | gap |
| 20–23 | capacity sliver (3pt wide, 12pt tall, vertically centered) |
| 23–26 | trailing pad |

The badge is drawn **overlapping** the glyph's top-right corner (a 9pt filled disc centered at ~(13.5, 13.5)) — it never adds width. When there is no sliver, the sliver zone is drawn empty, not collapsed. Set `statusItem.setLength_(26.0)` explicitly rather than relying on variable length, and set `statusItem.button().setImagePosition_(NSImageOnly)`.

**Template-image discipline:** call `setTemplate_(True)` on the composite. Template images preserve alpha, so the sliver renders as *track at 25% alpha, fill at 100% alpha* within a single monochrome image — AppKit tints it correctly for light, dark, Tahoe's transparent wallpaper-tinted bar, and Tahoe 26.1's "Tinted" mode. **The only non-template element is the ask badge**, composited on top as a real color (Mail/Dock convention: color is reserved for a count badge, never for the base glyph).

Also: `statusItem.setAutosaveName_("jrbar-status")` — currently never called, so position doesn't persist and hiding utilities can't index it stably (CodexBar uses stable per-provider identifiers for exactly this).

### The three renderings

**AT REST** (all mains idle, nothing needs you, no lane past threshold):
- Glyph: `circle` (STATE_IDLE), template.
- Sliver: **absent** (drawn as empty). Nothing is near a cap; do not spend pixels.
- Badge: none. Title: `""` permanently.
- No animation. Ever, at rest.

**AGENTS WORKING** (≥1 main working, nothing blocked):
- Glyph: `arrow.triangle.2.circlepath` — but **worst state across MAIN sessions only**, using the existing STATE priority ordering (status_bar.py:912-916). Sub-agents never influence the glyph; a main that has handed off to 80 sub-agents reads as Working, not Idle.
- Sliver: shown **only when the most-consumed lane is past 60% used** — auto-select the single closest-to-cap lane (CodexBar's `providerWithHighestUsage()` pattern); never try to show Codex and Claude simultaneously in 3pt. Fill height = `remaining_pct/100 × 12pt`, drawn from the bottom. **Static.** No animation on the sliver, at any time.
- Badge: none.

**SOMETHING NEEDS YOU** (≥1 main in ASK/BLOCKED):
- Glyph: `questionmark.circle` (ask) or `exclamationmark.triangle` (failed).
- Badge: **shown at `ask_count >= 1`, not `>= 2`.** The owner's stated requirement is to be told about *one* blocked agent. 9pt non-template filled disc in `systemOrangeColor` (ask) / `systemRedColor` (failed), with the count in 8pt `monospacedDigitSystemFont` white. Monospaced digits so 1→7 doesn't reflow the glyph inside the fixed canvas. Collapse to `9+` above 9.
- Sliver: same rule as Working.

### Motion: one-shot, transition-only
`NSSymbolEffect` cannot be applied to `NSStatusBarButton.image` (Apple Dev Forums 747451). Host an `NSImageView` as a subview of `status_item.button()`, sized to the same fixed 26×18 frame, and call `addSymbolEffect` on **that view**. Fire a single `.bounce` (or `.pulse` with `options: .nonRepeating`) at exactly two moments: a main transitions **into** Ask, or a main transitions **into** Done-unseen. Then it settles static. macOS 14+ only; below that, no animation and no fallback shimmer.

**Never** run a sustained pulse. LEDs are the peripheral interrupt surface; the status item is the ledger. Ambient motion in the menu bar is the exact noise the LED layer exists to absorb.

### Right-click: don't
macOS Tahoe has an unfixed bug (Dev Forums 811718, Jan 2026, 0 official replies) where right-clicks at the very top edge of the 30pt menu bar miss the status item's hit test while left-clicks land. Keep the current `status_item.setMenu_()` click-opens-menu design (status_bar.py:2134) and build **no** distinct right-click behavior.

### Accessibility (currently an accident waiting to break)
Today VoiceOver works only because `button()` is a real `NSButton` and `image_for_symbol` passes an `accessibilityDescription` (status_bar.py:15399-15409). The explicit setters in `_apply_status_accessibility_text` (5872-5897) fire **only** on the ResolvedGlance emphasis path; the everyday `set_status` path never calls them. Once the icon is hand-composited, that free accessibility disappears — this is literally CodexBar's open bug #859 ("custom-rendered menu bar icons ... not exposed to VoiceOver").

**Rule: `set_status` must call `setAccessibilityLabel_` / `setAccessibilityValue_` / `setToolTip_` on every single invocation, unconditionally**, not just under glance emphasis. Label: `"JR-BAR"`. Value: `"2 agents need you · 3 working · Claude Opus 18% left"`. Also rename the tooltip prefix — it still says `"SidePulse Agent Monitor:"`.

### Optional text mode
Keep `menu_bar_label_enabled` as an opt-in, but when on, reserve a **fixed-width** text zone using `monospacedDigitSystemFont` and a fixed character budget (e.g. 8 chars, right-padded), so the total item length is still a constant. Never let the label grow and shrink.

## The dropdown

## A2. The dropdown, top to bottom

**Hard budget: 15 rows.** Everything that doesn't earn a row goes to Settings or the Agent Browser.

### Row 1 — attention line (disabled)
`"3 active · 1 needs you"`

Drop the `"Agent Mailbox · "` prefix from agent_browser_window.py:100-108. `build_menu`'s own comment (status_bar.py:13361-13363) says "no self-titled header (you know what menu you clicked)" and the current row violates it.

**Append a capacity clause only when a lane is past threshold:** `"3 active · 1 needs you · Claude Opus 18% (≈2h left)"`. Threshold default 70% used, configurable. This is the single change that lets the owner stop opening CodexBar — the thing he checks is in the first row, not six sections down past Focus/Devices/Profiles/Timer.

### Rows 2–6 — up to 5 MAIN sessions, always all of them
At 1–5 mains you never need shelves, pagination, drag-reorder, or search. Show every main, always.

**Sort by attention tier**, reusing t3code's `THREAD_STATUS_PRIORITY` verbatim: Pending Approval (6) > Awaiting Input (5) > Working/Connecting (4) > Plan Ready (3) > Monitoring (2) > Completed (1). Then, **within a tier, sort by a static key (session start time) and never re-sort on activity.** t3code's governing invariant applies exactly: *position encodes lifecycle, color/label encodes live status, and conflating them is a bug class.* A ticking duration or a rising token count must never move a row while you're reading it. A row moves only when it crosses a tier boundary.

**Row anatomy — two lines via `setAttributedTitle_`** (an `NSAttributedString` containing `\n`, with a right-aligned tab stop; this keeps native highlight and keyboard behavior, unlike the custom `NSView` approach used for the usage lane):

```
Line 1:  [harness glyph]  sidepulse-manager-completion            Blocked
Line 2:  ● 12 working · 34 idle · 6 done                            4m
```

- Line 1: harness icon (`NSTextAttachment`, 12pt template symbol) → session/project label (`.byTruncatingTail`, the only element that truncates) → fixed-width right-aligned state word (Blocked / Working / Done / Ready). Color convention borrowed from t3code: amber = blocked, sky = working, emerald = done, secondaryLabel = ready.
- Line 2: **present only when the main has sub-agents.** This is the whole answer to "100 sub-agents without a wall": a **state-bucket rollup chip**, never a bare count, never one row per agent. `● 12 working · 34 idle · 6 done`. It degrades identically at N=3 and N=100. The trailing elapsed time is rendered once and **does not tick** — five live timers repainting in a small dropdown is exactly the ambient noise t3code explicitly removed ("a label that animates forever is noise in a sidebar full of them"). Only the currently-highlighted row may show a live duration.
- Icons, state word, and rollup numbers are fixed-width and never truncate. Only the session label gives way.

**Primary action = the row itself, and it means "reveal the real terminal."** Today the row has a submenu, which in AppKit *suppresses its own action* — so there is currently no way to click a main. Fix by making the row a plain actionable item (`revealAgent:`) and moving operator actions to an **Option-key alternate item** (`setAlternate_(True)` + `NSEventModifierFlagOption`), a native macOS idiom that costs zero visible rows. The full action set stays in the Agent Browser. This matches the owner's stance exactly: *"I'm using it to see what's happening, where I can click through and manage them myself."*

**Keyboard:** `setKeyEquivalent_("1"..."5")` on the main rows (Cmd+N jumps to the Nth *visible* row, always matching rendered order — t3code's rule that jump keys can never point at an invisible row).

**Sub-agent detail is not in the dropdown.** No expansion, no nested list. If you want individual sub-agents, the rollup chip is a click away from the Agent Browser, which already has room for AgentsPanel-style workflow/phase grouping (`{active} active · {settled} done` headers, dot-strip texture on collapsed phases).

### Row 7 — overflow (only if >5 mains)
`"2 more…"` → opens Agent Browser scoped to NEEDS_YOU. Keep as-is.

### Row 8 — actionable failures, inline, only when present
Keep the existing pattern that already works: `"⚠ Claude hooks are out of date — reinstall in Setup…"` (status_bar.py:13385-13396) and device write errors. **Extend it to capacity:** `"⚠ Claude usage: sign in required"` with a click that fixes it. Error detail must be **inline, never hover-gated** — t3code hides error text in a tooltip (`SidebarThreadTooltip`), which is fine for a sidebar you stare at and useless for a 2-second glance. Do better than the source here.

### Row 9 — separator

### Row 10 — the usage lane (custom NSView, replaces `build_usage_menu_item`)
See part B. Structurally: it stays a single `NSMenuItem` hosting one `NSView` so live refreshes never rebuild the menu (that part of today's design is right), but it grows from 2 hardcoded provider slots to N lane rows.

### Row 11 — separator

### Row 12 — Devices (one row + submenu)
Collapse today's per-device rows into `"Devices (2)"` with a submenu. **Exception preserved:** a device whose writes are failing keeps its inline `⚠` row at top level — that ENOSPC freeze sat invisible for 13 minutes and the existing comment is right to insist on it. `Add/Remove Screen Bar` moves inside the submenu.

### Row 13 — separator
### Row 14 — `Setup…` / `Settings…` (Cmd+,)
### Row 15 — `Quiet for an Hour` / `Quit JR-BAR` (Cmd+Q)

### Loading and empty states
- **Loading: render the last-known cached snapshot immediately.** Never flash blank on open. t3code has no loading pattern at all (the `SidebarMenuSkeleton` in its shadcn kit has zero call sites) — that's a gap in the reference, not a design choice. A glance surface that goes blank on open defeats its own purpose. A real spinner is reserved for genuine cold start with zero cache.
- **Empty, zero mains:** row 1 becomes `"No agents running"`, row 2 `"Start a session in Claude Code, Codex, or Cursor and it appears here."` (AgentsPanel's tone, verbatim in spirit).
- **Unread/attention is derived, not stored.** Adopt t3code's model wholesale: `needs_you = latest_turn.completed_at > last_visited_at`, with `last_visited_at` client-local, per-device, debounce-persisted. A never-visited session reads as *read* (so first launch doesn't light up every historical session). Opening the menu marks visited. **Wire the LED interrupt trigger and the row's Done/Blocked state off the same derived value** so the two surfaces cannot drift.

## The usage lane (replacing CodexBar)

## B. The usage lane that replaces CodexBar

### B1. Data model — keep ours, it's already better

Do **not** port CodexBar's `UsageSnapshot`. It is a grab-bag with ~15 provider-specific optional fields bolted onto one shared struct (`deepseekDetailedUsageState`, `opencodegoUsage`, `mistralUsage`, `commandCode*`…). Every new provider leaks into the shared type.

Our `capacity_types.py` is already the right shape and is *stricter* than what we're imitating:

- `QuotaLaneKey { source, opaque_scope, pool, model, window, effect }` with `QuotaEffect ∈ {ALL_WORKLOADS, MODEL, FEATURE, UNKNOWN}`, and a `__post_init__` invariant: `effect == MODEL ⟺ model is not None`. CodexBar has *no* first-class model-scoping concept — it erases scope into a title string (`"Opus only"`) during OAuth mapping. Ours keeps it structural.
- `ObservationState ∈ {OBSERVED, OBSERVED_ZERO, NULL, UNAVAILABLE, PARTIAL, STALE, LAST_KNOWN_GOOD}` and `SourceHealthKind ∈ {HEALTHY, REFRESHING, COOLDOWN, SIGN_IN_REQUIRED, ACCESS_DENIED, TIMED_OUT, UNSUPPORTED, PARTIAL, FAILED, STALE}` — strictly more expressive than CodexBar's 4-value `UsageDataConfidence`, and unlike CodexBar's, actually enforced at construction.

**Three edge cases from CodexBar worth encoding (they are real, hard-won multi-provider scars):**
1. **Never synthesize a placeholder lane.** CodexBar's `isSyntheticPlaceholder` exists because Claude-web returns a fake 0% five-hour window when there's no live session. Our answer is simpler: an unreported lane is *absent*, not 0%. `OBSERVED_ZERO` means we measured zero; `NULL` means the provider said nothing. Never render a NULL lane.
2. **Reset known, percent unknown** (CodexBar's `usageKnown: Bool`) → represent as `ObservationState.PARTIAL` with `reset_epoch` set and `used_percent = None`. Show the reset, show `—` for the number. Never render a fake exhausted quota.
3. **Raw vs display-clamped percent.** Keep `used_percent` unclamped in the model (pace math must see >100%); clamp only in the formatter. This is CodexBar's `UsagePercent.raw / .displayClamped` split and it's correct.

**The blocking gap:** `claude_quota.fetch_windows()` (claude_quota.py:49-51) unconditionally raises `ClaudeQuotaUnavailableError('claude_remote_quota_unsupported')`. There is no network call, no OAuth read, no Keychain access anywhere in `src/sidepulse`. Meanwhile `windows_from_payload` (claude_quota.py:68-197) is a **correct, complete parser** that already reproduces CodexBar's schema including both the legacy `seven_day_opus`/`seven_day_sonnet` fields and the newer `limits[]` array with `scope.model.display_name` extraction and all-models-scope filtering.

**Build item #1, and it's bounded:** read the `claude` CLI's OAuth token from the macOS Keychain, `GET https://api.anthropic.com/api/oauth/usage` with `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`, `User-Agent: claude-code/<version>`, hand the JSON straight to the existing parser, delete the raise. Template to port: CodexBar's `ClaudeOAuth/ClaudeOAuthUsageFetcher.swift:60-127` and `ClaudeOAuthCredentials+SecurityCLIReader.swift`. Populate honest `ObservationState`/`SourceHealthKind` on every outcome — 401 → `SIGN_IN_REQUIRED`, 429 → `COOLDOWN` with `retry_at` from `Retry-After`, network fail → `FAILED`. **Do not default silently**; that is precisely the trap CodexBar fell into (its confidence enum is populated in 2 of ~10 provider paths).

### B2. Burn rate and time-remaining — the math

> **Overtaken by events (2026-08-26).** The quota-forecast plane this
> section analyzes — `capacity_forecast`, `capacity_calibration`, the
> forecast release authority — was deleted in the 0.5.0 coalescence. It
> never reached a user. The Quota Runway display renders from the usage
> plane's own gated lanes instead. Kept as written for the math record.

Ours (`capacity_forecast.py`) is already more defensible than CodexBar's. **Do not port their algorithm.** CodexBar emits a point ETA from single-snapshot linear extrapolation (`UsagePace.weekly`: `rate = used/elapsed; eta = (100-used)/rate`), or for Codex-weekly only, a weighted-median curve reconstruction. Neither refuses; both can emit confident nonsense.

**State the math we ship:**

Per lane, per cycle (a cycle is delimited by `reset_epoch`; a changed `reset_epoch` starts a new cycle and prior samples are discarded, never blended):

1. Sample series `(t₀,u₀) … (tₙ,uₙ)` from `capacity_history`, `u` = raw used-percent.
2. Per-interval slope `rᵢ = (uᵢ − uᵢ₋₁) / (tᵢ − tᵢ₋₁)` in %/hour. Reject intervals with `Δt < 60s` (quantization noise). Any `uᵢ < uᵢ₋₁` within a cycle → **refuse** (`NON_MONOTONIC`), don't smooth it away.
3. Robust center and spread — median and MAD, not mean and σ, because a single burst must not dominate:
   - `m = median(rᵢ)`
   - `MAD = median(|rᵢ − m|)`
   - `r_lo = max(0, m − 2·MAD)`, `r_hi = m + 2·MAD`
4. **Exhaustion range, not a point:**
   - `t_earliest = (100 − u_now) / r_hi`
   - `t_latest   = (100 − u_now) / r_lo`
5. Clamp to the cycle: if `t_earliest ≥ time_to_reset`, emit **"holds to reset"** and no ETA. If `r_hi ≤ 0`, refuse (`NO_POSITIVE_BURN`).
6. **Refuse rather than guess** — the existing ~30 typed `ForecastRefusalCode`s already cover stale source, cross-lane/cross-account history contamination, insufficient cycle elapsed (<3%), insufficient slopes (<3), insufficient slope coverage, reset instability, unbounded runway, exhaustion past reset.
7. **Confidence:** `HIGH_HISTORICAL` at ≥5 complete prior cycles; `MEDIUM_OBSERVED` from in-cycle robust slopes; `LOW_LINEAR` from naive `u/elapsed`.
8. **Calibration gate — this is what CodexBar has no equivalent of and it should stay.** `capacity_calibration.py` runs a rolling-origin backtest that must beat a naive baseline and pass false-warning-rate/miss-rate regression checks before `ForecastReleaseState` becomes `AUTHORIZED`. **Numbers render only when AUTHORIZED.** `WITHHELD`/`REVOKED` → show the pace word only (see B4), never a number.

*Optional enhancement worth taking from CodexBar:* recency-weight prior cycles by `exp(−age/τ)` with τ ≈ 3 cycles to build a per-weekday consumption-shape prior for the weekly window — but use it **only to widen or narrow the [earliest, latest] bounds**, never to produce a point estimate. This is the one real capability they have (weekly curve-shape reconstruction) that we don't.

**Build item #2 is pure integration, not algorithm work.** `analyze_capacity_forecast` and `capacity_calibration`'s `ReleasedForecast` still have **zero call sites** in `status_bar.py`. `build_capacity_detail` no longer does: the "Why Is It Doing That?" panel calls it every time it renders, and passes `forecast_view=None`, which is why the panel prints *"Forecast unavailable — No released forecast"* today. That `None` is the whole of build item #2 — the seam exists and is exercised; only the forecast argument is missing. `build_capacity_card` remains uncalled, and cannot be called usefully until a `CapacityAccountBinding` exists (see the note on `MAX_CAPACITY_CARD_ROWS` in `capacity_view.py`). Five test files prove the math in isolation. No user has ever seen a burn-rate number from this app.

**Build item #3:** write `format_forecast(status, now)` next to the existing `format_reset` / `format_remaining` / `format_freshness` in `capacity_view.py`. `_forecast_status` (capacity_view.py:882-910) currently emits only `"Forecast available"` plus two raw epoch floats.

Formatting rules:
- Both bounds finite and `t_latest / t_earliest ≤ 3` → `"≈2h10m–4h30m left"`.
- Ratio > 3 → the range is too wide to be useful as a range; show the lower bound only: `"≈>2h left"`. **Never** synthesize a midpoint — false precision is the failure mode this whole design exists to prevent.
- `t_earliest ≥ time_to_reset` → `"holds to reset"`.
- Burn rate field shows the **median only**: `"≈12%/h"` (one decimal below 10, zero above). The bounds live in the ETA, not duplicated here.

### B3. Resets
`format_reset` (capacity_view.py:500-527) already matches CodexBar's countdown bucketing exactly — `Resets in Xd Yh` / `Xh Ym` / `Xm` / `Resets now`, minutes always ceil'd, seconds never shown. **Done, no work needed.**

Two additions:
1. **Absolute style as a display toggle** (CodexBar's `ResetTimeDisplayStyle`): same-day → time only (`09:00`); next day → `tomorrow, 09:00`; else → `Aug 19, 09:00`. **Default: countdown in the row, absolute in the tooltip** — you get both without a setting most people will never find.
2. **Carry a cached reset forward** when a fresh payload omits one (CodexBar's `backfillingResetTime`), so a transient API hiccup doesn't blank the countdown — but mark the carried value `LAST_KNOWN_GOOD`, never `OBSERVED`, and refuse the carry-forward when the lane's window duration differs from the cached one (their Amp carve-out generalized properly instead of hardcoded per-provider).

### B4. Per-provider model-tier splits

**The bug to fix first:** `capacity_menu_lines` (status_bar.py:12531-12568) does `primary_window = model.windows[0]` and renders only that. `model.windows` is plural; Claude's Opus and Sonnet lanes are in there and never reach the screen. And `build_usage_menu_item` (12585-12641) hardcodes exactly two rows (`codex`, `claude`) at fixed y-offsets in a fixed 292×110 view.

**Replacement lane view:** iterate all lanes, group by source, order within a source as:
1. `effect = ALL_WORKLOADS` lanes first, shorter window before longer (5-hour before 7-day).
2. `effect = MODEL` lanes after, **sorted by remaining ascending** — closest to exhaustion first, because that's the one that will stop you.

**Claude:** implement the `limits[]` / `scope.model.display_name` path, **not** the legacy `sevenDayOpus ?? sevenDaySonnet` fallback — the fallback silently drops one tier when both exist. Our parser already does this correctly (claude_quota.py:129-184); just route the results through `QuotaLaneKey(effect=QuotaEffect.MODEL, model="opus"|"sonnet")` via the existing `adapt_legacy_usage_windows` bridge. Titles: `"Opus only"`, `"Sonnet only"`, plus whatever promo model names appear (a model literally named "Fable" showed up in CodexBar's wild).

**Codex:** there is **no verified per-model split** — its `rateLimitsByLimitId` dictionary has no `scope.model` equivalent. Codex's real split is a different axis: three independent tracks — rate-limit windows, a monthly $-denominated credit cap, and bonus/promotional credits with their own expiry (`CodexRateLimitResetCreditsSnapshot`). Model those as separate lanes with distinct `pool` values, not as model tiers. Our Codex sourcing (local transcript parsing in `usage_stats.py:582-706`, cached by `(size, mtime)`) is already wired end-to-end and is *better suited* to a menu-bar app than CodexBar's live-process RPC — it works when the CLI isn't open.

**Subscription tiers, not API keys.** CodexBar's architecture agrees with the owner's instinct: the subscription percent-of-quota view and the Admin-API cost/token view come from different endpoints with different credentials and are *different data shapes*, not two renderings of one number. Our `provider_capacity.py:313-329` already correctly models Admin-API as billing-month lanes; the missing piece is purely the individual OAuth subscription path (5h / 7d / model-tier %). One note worth encoding: Claude **Enterprise** does not count as a subscription — its "extra usage" figure is a hard spend *limit*, not a bonus pool.

### B5. Provenance and confidence — the rule that keeps estimates from masquerading as facts

**One typographic law: measured facts render plain; derived numbers always carry `≈` and always render in `secondaryLabelColor`.** No exceptions, no settings toggle.

| kind | example | rendering |
|---|---|---|
| Measured percent | `18% left` | plain, `labelColor` |
| Measured reset | `resets in 4h 12m` | plain, `labelColor` |
| Derived burn | `≈12%/h` | `≈` prefix, `secondaryLabelColor` |
| Derived ETA | `≈2h10m–4h30m left` | `≈` prefix, `secondaryLabelColor` |
| Carried forward | `18% left · 6m ago` | plain value, age suffix in `tertiaryLabelColor` |
| Never measured | `—` + one-word reason | `tertiaryLabelColor` |

Further rules:
- **A refused forecast is omitted, not labeled "unavailable."** Empty space is honest; "Forecast unavailable" is clutter that trains you to ignore the row. The refusal code goes in the tooltip for debugging. **One exception:** refusals the user can *act on* (`SIGN_IN_REQUIRED`, `ACCESS_DENIED`) get a real inline row with a click that fixes it, promoted to the dropdown's actionable-failure slot (row 8).
- **Staleness degrades in place, doesn't vanish.** Past 1× refresh interval, append `Xm ago`. Past 3×, drop the value to `tertiaryLabelColor` and mark `STALE`. Never blank a number you had a moment ago — that reads as breakage.
- **Never synthesize.** No 0% placeholders, no averaged midpoints, no interpolated resets.
- **Do not add a CodexBar-style `UsageDataConfidence` field.** Copying the enum's names is fine; copying its population is not — it's stamped in 2 of ~10 paths and defaults to `.unknown` everywhere else, and it's surfaced in no UI. `ObservationState` + `SourceHealthKind` already carry more, are validated at construction, and just need every *new* source to populate them honestly.
- **Format currency in forced `en_US` locale** if/when dollar figures appear — CodexBar learned this the hard way (non-US users seeing `$54,72`).

## Alcove coexistence

## Alcove coexistence fix list

Ordered by user-visible impact. All references are `src/sidepulse/virtual_device.py` unless noted.

### 1. The 4–6pt bracket inset — one-line fix, in `reposition()`, not in drawing code
**Root cause traced end to end.** The live path takes `observation.width` raw from `AlcoveObservationReducer.current()` with zero margin; `virtual_window_frame_for_screen` (366-372) then sets the window to *exactly* that width (`target = max(140.0, alcove_total_width)`); then `_draw_wings_only` (1475) calls `alcove_accent_horizontal_bounds(width)` (1488), which insets by `ALCOVE_ACCENT_EDGE_INSET = 6.0` (148, function 576-579) before drawing the underline, the rounded-rect clip, the Alcove-body clip, and the risers. Everything visible is drawn 6pt inside a pixel-exact window.

The 6pt inset is **not** wrong in isolation — its comment (145-148) correctly explains it keeps antialiasing bloom off the transparent window edge, which was a real problem back when the window had generous margin. It only became a defect when Alcove-following started sizing the window exactly to content.

**Fix: widen the window, don't shrink the content.** In `reposition()`'s Alcove-follow branch, request `observation.width + 2 * ALCOVE_ACCENT_EDGE_INSET`. Rehabilitate the currently-dead `ALCOVE_CAPSULE_MARGIN = 2.0` (123) for this — it's plainly what it was originally for. **No change to any drawing code.**

### 2. Stop guessing the window level — measure it
`ABOVE_ALCOVE_WINDOW_LEVEL = 2147483630` (89) is a hardcoded literal (INT32_MAX − 17) with a comment claiming it's one above Alcove's overlay. Nothing in `src/sidepulse` calls `kCGWindowLayer`, `CGWindowLevelForKey`, or `kCGMaximumWindowLevel` — it is never validated.

But `_alcove_window_values()` (441-484) **already** calls `Quartz.CGWindowListCopyWindowInfo` and iterates the result dicts for `kCGWindowNumber` and `kCGWindowBounds`. Those same dicts carry `kCGWindowLayer` — Alcove's real, live Z-order. It's simply never read.

**Fix:** read `kCGWindowLayer` from the dict already in hand, cache it on the existing 3s `_alcove_running_cache` TTL, set our level to `measured + 1`, and keep the constant only as a fallback when no Alcove window is found and as an upper sanity clamp. Failure mode today is silent and total: our bracket renders *under* Alcove's opaque backdrop with no error — the exact thing the wings-only design exists to prevent.

### 3. Compact mode is blind to Alcove — a one-word gate change
`wings_only = alcove_active and self.wraps_menu_bar` / `compact = alcove_active and not self.wraps_menu_bar` (2588-2589) are mutually exclusive. The measurement block that produces `follow_width`/`follow_center_x` (2621-2695) is gated on `if wings_only and …`, so it **never** runs in compact mode; the `else` at 2696 calls `_stop_alcove_observer()` outright. `_draw_compact_accent` (1748-1777) draws across `self._notch_geometry()`, which only ever reflects hardware/user notch width.

Result: with "wrap the menu bar" off, our accent sits at fixed hardware-notch width no matter what Alcove's capsule is doing — and once a Live Activity expands the capsule, the file's own design promise (71-79: "a status accent under Alcove's own shape, not a second competing widget") is broken; it becomes a centered sliver under a much bigger shape.

**Fix:** (a) loosen the measurement gate to `if (wings_only or compact) and …` — the reducer already produces mode-agnostic `center_x`/`width`; (b) loosen `virtual_window_frame_for_screen`'s separate `wrap_menu_bar`-only gate on `alcove_total_width`; (c) at minimum use `follow_center_x` so the accent tracks the capsule's true center. "Compact" per the code's own comment means *no black backdrop/glow* — it was never supposed to also mean *pinned to hardware width*. Those two got coupled by accident.

### 4. Delete the dead capture subsystem as ONE unit
It's bigger than the logged `AlcoveCapsuleTracker` (493-567). Delete together:
- `measured_alcove_capsule_width()` (487-490) — a stub whose entire body is `del menu_band_height; return None`.
- `AlcoveCapsuleTracker` — constructed only in `tests/test_sidepulse.py` (~16865-16908), never in production.
- Seven constants duplicating `alcove_observation.py`'s real ones: `ALCOVE_CAPSULE_ALPHA_THRESHOLD`, `ALCOVE_CAPSULE_MAX_BAND_FACTOR`, `ALCOVE_FOLLOW_MAX_WIDTH`, `ALCOVE_NARROW_AFTER_SECONDS`, `ALCOVE_HOLD_SECONDS`, `ALCOVE_CAPSULE_MARGIN` (unless rehabilitated per fix #1), `ALCOVE_MEASURE_TTL_SECONDS`.
- Its three tests: `test_tracker_widen_instant_narrow_patient`, `test_tracker_holds_through_gaps_then_falls_back`, `test_tracker_caps_the_balloon`.

**Why together:** these tests *pass*, and they validate correct-looking hysteresis/margin behavior for code the app never runs. That's worse than dead code — it's dead code with green tests, giving false confidence that margin behavior is verified in production when the live reducer applies no margin at all (`test_alcove_observation.py` has no margin test, correctly, because there is no margin). Removing the class but leaving the stub/constants/tests just re-poses the "is this dead?" question to the next reader. Nothing is lost: the reducer's real tests already document the intended hysteresis.

### 5. Quit detection latency (~3–5s) — polish
`is_alcove_running()` is cached 3.0s (2579-2587) to avoid re-enumerating apps at up to 4Hz. After the user quits Alcove we can sit in wings-only/compact at an elevated window level for several seconds. **Fix:** add an `NSWorkspace.didTerminateApplicationNotification` observer that invalidates `_alcove_running_cache` immediately. Low urgency, low cost.

### 6. Multi-monitor — verify on real hardware before claiming this works
`reposition()` always operates on `NSScreen.mainScreen()` (2562, 2094, 2184 — no per-screen iteration exists anywhere in the file), while `is_alcove_running()` is a global process check with no screen awareness. `alcove_active` alone drives the mode split at 2588-2589. On a multi-display setup where Alcove's window isn't on the current main screen, we'd still force accent-only rendering everywhere. **Unverified from static reading — flag as needs-hardware-check, don't ship a fix blind.** The correct fix if confirmed: gate on *Alcove window present on this screen*, not on global process presence.

### 7. Re-tune the sanity bands against Alcove v1.7's new shapes
Alcove v1.7.0 (Apr 2026) added a **pill shape for notchless displays**, **notch outlines** (an Alcove-drawn decoration around the physical notch — undocumented visual relationship to our bracket, needs an eyeball check), and **Duo mode** (two live-activity widgets side by side, wider than either alone). v1.7.1 then added a force-simulated-notch toggle, implying the pill became a default some users rejected.

Our capture bands (`ALCOVE_MAX_BAND_FACTOR = 1.8` × menu-band height, 40–520pt width) were presumably tuned against the notch-shaped capsule. **Spot-check Duo and pill footprints on real hardware before trusting them.**

### 8. Two facts to correct in our docs
- Alcove's site is **tryalcove.com**, not henrikruscon.com (which doesn't resolve). It sells direct via Stripe — **there is no App Store listing**, so any "check the App Store listing" copy or App-Store-sandboxing assumption is wrong. It's a self-updating direct download that openly depends on private APIs.
- **Alcove is not dead.** Its GitHub releases mirror was archived 1 Jun 2026, but tryalcove.com's own changelog shipped v1.7.3 through v1.7.9 *after* that date. Don't infer abandonment from the archived mirror.

**Summary of the coexistence spec:** absence and resize handling are already good (widen-instantly / narrow-after-3s / hold-through-gaps-8s, genuinely tested). Fixes 1–4 are the real work. 5 is polish. 6–7 need hardware.

## Onboarding

## Onboarding techniques worth stealing

### From Alcove — the core technique: lazy, contextual permission asks
Alcove requests each permission **at the moment the user enables the specific feature that needs it**, never as a first-run wall. Its changelog shows this as an ongoing discipline, not a one-time design: *"1.2: reworked onboarding"*, *"1.7.0: Added calendar onboarding, Added accessibility onboarding"* — every new permission-requiring feature ships with its own onboarding step at the point of introduction. First run is only two toggles (launch-at-login, menu-bar icon), then per-category notification toggles, then lock-screen config.

**Apply concretely:**
- Screen Recording (for Alcove alpha capture) is asked **only** when the user turns on "Follow Alcove," with a one-line consequence: *"Without this, the bracket stays at hardware notch width and won't track Alcove's capsule."*
- Keychain access for Claude usage is asked **only** when the user enables the Claude capacity lane: *"Reads the same token the `claude` CLI already stores. Without it, Claude usage stays blank."*
- Accessibility/AppleEvents (for reveal-in-terminal) is asked on the **first click of a session row**, not at install.

The pattern: *feature toggle → explain what breaks without the permission → ask*. Never a permissions dialog cascade at first launch.

### From Alcove — avoid invasive permissions by listening instead of grabbing
Alcove deliberately never requests keyboard access; it listens to system change notifications (brightness, volume) and paid for that with edge-case work (headphone auto-adjust, adaptive brightness producing false triggers). That's the right trade and we already make it in spirit — the hook-based event model is our version. Keep resisting anything that needs Accessibility or Input Monitoring globally.

### From the code we already have — keep this, it's the best pattern in the repo
The `"⚠ Claude hooks are out of date — reinstall in Setup…"` row (status_bar.py:13385-13396) with its comment *"this failure was invisible for an hour the first time it happened."* **This is onboarding done right: a failure that surfaces where the user already looks, with the one click that fixes it.** Extend the pattern rather than inventing new mechanisms:
- Capacity sign-in required → same row shape, same one-click fix.
- Device write failure → already there, keep it.

### Setup must *verify*, not just instruct
An install step that says "hooks installed ✓" and then no events arrive is the failure mode that cost an hour. **Add a final Setup step that waits for the first real hook event** and shows it live: *"Start a session in Claude Code… waiting… ✓ Received `SessionStart` from claude-code 2s ago."* Nothing is "installed" until an event round-trips.

### Empty states: context-specific copy, never one generic string
t3code ships five distinct empty strings for five distinct emptinesses (`"No projects yet"` / `"No threads in {project} yet"` / `"No threads yet"` / `"No threads found"` / `"No agents yet"` + one explanatory sentence). Do the same — a first-run empty menu should read *"No agents running. Start a session in Claude Code, Codex, or Cursor and it appears here."*, which is different from the steady-state *"No agents running."*

### Do better than t3code on loading
t3code has **no loading pattern at all** — a cold sidebar and an empty sidebar render identically (its `SidebarMenuSkeleton` has zero call sites; it's dead shadcn scaffolding). Don't copy the gap: **render the last-known cached snapshot instantly on open** (stale-while-revalidate), with a real loading indicator reserved for genuine cold start with zero cache. A glance surface that flashes blank on open defeats its own mandate.

### What NOT to steal
- **CodexBar's confidence system.** `UsageDataConfidence` is populated in 2 of ~10 provider paths, defaults silently to `.unknown` everywhere else, and is surfaced in no UI at all. Aspirational, not implemented. Copying its enum names is fine; copying its discipline is not available to copy.
- **The daily Tip carousel we currently ship.** Tips are onboarding that never ends, occupying steady-state menu rows forever. Replace with the contextual-step model above and delete the tip system (see deletions).

## From the community

## From the community research — what to adopt

**Calibrate expectations first:** upstream `inteliwear/sidepulse` is ~1 month old (created 2026-07-15), 28 stars, 9 forks, 12 open issues+PRs, solo-maintained, MIT. Zero Discussions, no Discord, no Reddit, no YouTube, one Italian blog post (macitynet.it). This is not a movement — the entire real signal is concentrated in the forks and PRs. Don't build for an audience that doesn't exist yet; do harvest the engineering that does.

### 1. `adamstambouli/sidepulse` "fleet mode" — read this diff before writing our own LED fleet code
5 commits ahead of upstream. He independently arrived at our locked design: **sub-agents never claim a band or a menu row of their own** — *"three real sessions would otherwise present as eight"* — but their work still drives the parent's band, so a session that has handed off reads as Working rather than going dark. One LED band per codebase (8 LEDs for one agent, 4+4 for two, 3/3/2 for three), merging into one full-width animation when all agents share a state, sticky slots per codebase so a finishing agent doesn't reshuffle neighbors, multiple sessions in one directory collapsing to a single band.

**Specific engineering to lift, independent of fleet mode:**
- **Luminance-matched palette.** Green reads ~3.5× brighter than blue at equal drive level; he scales every color to blue's Rec.709 luminance. Our colors are almost certainly unbalanced the same way.
- **Working: cyan → blue.** Cyan and Done-green read as the same hue through a diffused LED. This is a real perceptual bug, not a taste call.
- **Don't hold "Done" at full brightness for 20 minutes** — worst case for glare and LED wear. Decay it.
- **SessionEnd releases LEDs immediately** rather than holding a Done band.
- **Retire sub-agents that outlive the session that spawned them**, and **stop sub-agents from posting an Ask nobody but the parent can answer** — both are bugs we will hit at 200 sub-agents if we haven't already.
- **Split Blocked/Error from Waiting-for-Input** as distinct states. Our `STATE_ASK` currently conflates them.
- **`scripts/preview_leds.py` generated from the live LED constants**, so the preview physically cannot drift from what the device receives. Adopt this idea wholesale — a hand-maintained preview is a lie waiting to happen.

### 2. `seanhellwig/sidepulse` — the adapter isolation rule
His OpenCode plugin (`opencode_plugin.js`, 142 lines) hooks OpenCode's event bus (`chat.message`, `tool.execute.before/after`, `permission.ask`, `session.idle/error/compacted`) and spawns a **detached `/bin/sh` fire-and-forget process** so a monitoring failure can never surface inside OpenCode itself.

**Make that a rule for every adapter we ship: monitoring must be incapable of breaking the thing it monitors.** Fire-and-forget, detached, no return path, no exception that can propagate into the host agent.

Also: he already answers upstream issue #12 (OpenCode/t3code support, filed by **Corey Quinn**). If we want OpenCode, start from his diff. And his Ghostty gotcha is real and non-obvious: Ghostty's `initial input` types the command into the new window's shell (window survives exit) whereas `command` *replaces* the shell (window closes on exit).

### 3. `djmango` PR #11 — privacy-tiered providers
Sulaiman Khan Ghori (founder of Based Hardware / Friend) contributed a **status-only** Cursor provider: Codex/Claude/Grok hooks write full session content — prompts, assistant messages, tool inputs — to a local JSONL decision log; the Cursor integration deliberately does **not**. It publishes only `working|done|ask|blocked|idle` transitions, with a test asserting no `message`/`prompt` field is ever present in the emitted event.

**Adopt the tier, not just the provider.** Offer a per-provider "status-only, no content log" mode with a test that enforces the absence. Someone contributing a *privacy-tiered design* unprompted is a strong signal about what people expect from a thing watching their coding sessions.

### 4. `nepeat` PR #5 — the installer guards
- **Refuse to install over a hand-written hook key.** His Hermes installer writes a marker-delimited managed block into `~/.hermes/config.yaml` and *refuses* if the user already hand-defines one of the managed events, because duplicate YAML keys silently shadow. Generalize: any config-file-editing installer must detect and refuse rather than silently win.
- He also found and fixed a **genuine bug in the existing Claude hook installer** while adding an unrelated provider. That path is fragile enough to deserve its own test suite — treat installer correctness as a first-class test target, not an afterthought.
- Splitting the monolithic `install.py` into an `install/` package (`_common.py` + one module per provider) is the right shape as providers multiply.

### 5. Upstream issue #4 / PR #6 — a bug we probably share
*"Codex Limit Reached"*: SidePulse didn't detect that a conversation stopped when it hit a usage limit, because there's no live Stop hook in that path. Fixed by reconciling terminal transcript states into the live monitor when `last_agent_message` is absent. **A usage-limit-hit must register as done.** Check our collector for the same hole — it's directly adjacent to the capacity work in part B, and an agent stuck in phantom-Working state is exactly the false signal that erodes trust in the LEDs.

### 6. `leog/ai-pulse` — the loopback API as a second integration surface
A software-only reimplementation (29 stars, created 2026-08-10) that explicitly credits SidePulse. Instead of a write-a-file DSL, it exposes a **loopback-only HTTP API** (127.0.0.1:7455, Keychain-stored bearer token, `POST /v1/agents/upsert`, `/v1/agents/{id}/event`, `GET /v1/agents`) so any local process can push state with `aipulse agent upsert --id … --state working`.

**Worth weighing seriously for us**, because it solves three of the owner's stated needs that a mounted-filesystem DSL doesn't: the **second Mac**, **cloud agents** (Claude/GPT code review), and any future **Screen Bar / notch** surface that isn't a physical device. A loopback token-authed endpoint is the natural ingest for "state from somewhere that isn't this filesystem."

Two more things from that project:
- **Direct validation of the universal-indicator thesis.** Its Show HN thread produced, unprompted: *"Shame its for Claude only and not a more universal harness."* — and that commenter then personally opened a PR adding a second harness, which was merged. People don't just complain about single-harness tools; they submit working integrations.
- **A lifecycle bug class to check for:** their extension auto-launched the app and then wrongly auto-*quit* a user-launched instance on session end. Fixed by tracking whether *this* process launched the app before allowing it to quit it. If we auto-launch any companion process, check for the same.

### 7. Community-validated integration targets, in rough priority order
OpenCode / t3code (issue #12, Corey Quinn — and a working fork exists), **Cursor** (PR #11, working), **Antigravity** (PR #7, camelCase PreInvocation/PostInvocation/Stop payload already reverse-engineered and validated against real events including `RESOURCE_EXHAUSTED`), **Hermes** (PR #5 — note it gates shell hooks behind a consent allowlist; hooks register but never fire until approved via TTY or `HERMES_ACCEPT_HOOKS=1`), **Kiro** (issue #3).

### 8. Packaging
PR #9's **isolated user installer** — a dedicated venv under `~/.local/bin` — instead of requiring `--break-system-packages` against Homebrew/system Python. Also `seanhellwig`'s unsigned-package build wrapper, if we want a distributable before signing is sorted.

## Deletions

## What to DELETE from today's menu

Ranked by rows reclaimed. Current `build_menu` (status_bar.py:13360-13596) emits ~20-28 rows.

1. **The `Profiles` submenu** (7 rows: 3 Apply + separator + 3 Save) — calibration/brightness slots are a configuration concern, not a glance concern. Move to Settings → Devices. **Reclaims 1 top-level row and all its cognitive weight.**

2. **The `Timer` / timebox submenu** — "the bar as an ambient countdown" is a cute feature and a wrong one for this menu. It has nothing to do with agents or capacity. Move to Settings, or cut it. **–1 row.**

3. **The daily `Tip` system entirely** — the `Tip:` item, its 3-item submenu (Show Me / Dismiss This Tip / Turn Off Tips), the separator above it, `daily_tip()`, and the dismissed-tips settings state. Tips are onboarding leaking into steady-state UI. Replace with the contextual-onboarding model (see the onboarding field). **–2 rows plus a settings key.**

4. **`Clear Finished (N)`** — obsoleted by the derived unread model. Opening the menu *is* the visit that clears "done since you last looked"; an explicit clear button plus a `cleared_session_ids` set is redundant state that can drift from the timestamp comparison. Delete the row and the set. **–1 row.**

5. **`Keep Awake With Lid Closed` submenu** — move to Settings. **Keep only the inline `Sleep warning:` error row**, which stays at top level (that instinct in the existing comment is right: errors never hide inside the submenu they came from). **–1 row.**

6. **Per-device rows when there is more than one device**, plus `Add Screen Bar` / `Remove Screen Bar` — collapse into one `Devices (N)` row with a submenu. Device *error* rows stay inline. **–2 to –4 rows.**

7. **The `Focus: …` line** — conditional, informational, and unrelated to "does anything want me." If a Focus is suppressing notifications that's worth knowing, so fold it into row 1 as a trailing clause (`… · Focus: Work`) rather than its own row. **–1 row.**

8. **The `"Agent Mailbox · "` prefix** on the summary row (agent_browser_window.py:100-108) — violates `build_menu`'s own stated no-self-titled-header rule.

9. **All title-string mutation in `set_status`** (status_bar.py:5934-5951): the `" (2)"` parenthetical, the `" ✓"` done marker, and the `state.label` text mode as currently implemented. Replaced by the composited fixed-width image. Keep `menu_bar_label_enabled` only if reimplemented with a fixed-width monospaced text zone.

10. **The two hardcoded provider slots in `build_usage_menu_item`** (`("codex", 64, 47), ("claude", 25, 8)` at fixed y-offsets in a fixed 292×110 view) and `capacity_menu_lines`' `model.windows[0]` — both structurally prevent per-model-tier rows from ever appearing.

11. **`"SidePulse Agent Monitor:"`** tooltip prefix — stale product name.

12. **In `virtual_device.py`, delete as one unit** (see the alcove field): `measured_alcove_capsule_width()`, `AlcoveCapsuleTracker`, seven duplicated constants, and their three green-but-meaningless tests.

**Net: a ~20-28 row menu becomes ~12-15 rows, and the top third is entirely agents + capacity** — the two things the owner actually opens it for.
