# JR-BAR — THE PLAN

Coalesced 2026-08-14 from: an exhaustive sweep of every SidePulse issue,
PR and fork; a reuse audit of CodexBar (c4ed34d0) and t3code against our
own source; and live read-only inspection of Alcove 1.7.9 and CodexBar
0.49.2 running on this machine.

Rule: anything CodexBar, t3code, or a community fork already solved well
is ADOPTED or PORTED with credit, never rebuilt.

## Wave 1 — Stop lying, stop stalling — confirmed defects, dead code, and the two cheapest latency wins. No new surfaces, no new permissions, all small. Ship this in a day.

### Sleep-prevention installer: stop trusting LaunchServices  ·  _small_  ·  **PORT from seanhellwig fork (main, +7) — credit in commit message**

**What.** /Users/jonathanreed/Documents/Codex/2026-08-12/hey-so-i-was-working-on/work/sidepulse-manager-completion/src/sidepulse/status_bar.py:14635 `open_terminal_setup_command` runs `subprocess.Popen([trusted_system_tool('open'), script_path])`. Bare `open` hands the .command file to whatever app owns the `.command` UTI. Replace with an explicit terminal resolution (reuse `GHOSTTY_APPLICATION_PATHS`-style absolute-path resolution already in status_bar_launch.py) invoked as `open -a <resolved bundle> <script>`, and stop reporting success from a non-raising Popen at status_bar.py:7710.

**Why.** Confirmed live bug in our own tree. On any Mac where Ghostty/iTerm2 owns .command, the Setup window says 'Sleep prevention installer opened' and nothing is installed. Silent false positive in the one flow the owner uses to make overnight agent runs work.

**Depends on.** nothing

**Proof.** Set a non-Terminal app as the default .command handler; click Set Up Sleep Prevention; a real terminal window opens and runs the script. Then unit test: `open_terminal_setup_command` called with a stub launcher asserts `-a` and an absolute app path are present in argv, and the caller reports success only after `sleep_helper_installed()` returns True on re-check.

### Alcove bracket sits 6pt inside the capsule: one-line fix  ·  _small_

**What.** virtual_device.py:2733 `follow_width = observation.width` → `follow_width = observation.width + 2.0 * ALCOVE_ACCENT_EDGE_INSET`. `_draw_wings_only` already insets the visible stroke by ALCOVE_ACCENT_EDGE_INSET (6.0, defined at virtual_device.py:155) from the window edge, and nothing ever adds that slack back, so the stroke lands 6pt inside Alcove's real corner on each side. `_alcove_body_path` already recenters generically, so no other change is needed.

**Why.** This is the visible defect on the owner's own screen every day. Measured root cause, exact constant, one line.

**Depends on.** nothing

**Proof.** screencapture the notch with Alcove idle, alpha-scan for the bracket stroke's outer x, compare against the measured Alcove backdrop edge (x≈661/851pt on this 14" MBP). Gap must be ≤1pt per side, was ~6pt. Add a pure-function test asserting the follow width exceeds the observation width by exactly 2×ALCOVE_ACCENT_EDGE_INSET.

### Compact mode never follows Alcove at all — fix both halves  ·  _small_

**What.** Two independent causes. (a) virtual_device.py:2673 gates the whole observation pipeline behind `wings_only`; loosen to `alcove_active and wing_override is None and follow_alcove_width` so measurement runs whenever Alcove is up. (b) `_draw_compact_accent` (virtual_device.py:1800) never reads `self.alcove_silhouette` — it sizes from `self._notch_geometry()` / hardware `notch_width`. Make it consume the silhouette's width and center when present, mirroring how `_draw_wings_only` consumes it via `_alcove_body_path`.

**Why.** Compact is the mode a user picks when they don't want us wrapping the menu bar — i.e. the polite mode — and it's the one that's been silently wrong. Same file, same session as the inset fix.

**Depends on.** nothing

**Proof.** Run with wraps_menu_bar off and Alcove running: the accent line's width/center track Alcove's measured capsule, not the 185pt hardware notch. Unit test: feed a synthetic AlcoveObservation of width 190 center 756 through the compact path and assert the drawn accent geometry matches, not the hardware slot.

### Delete AlcoveCapsuleTracker and its orphans  ·  _small_

**What.** Remove virtual_device.py:539-543 (`measured_alcove_capsule_width`, a stub that always returns None), 545-620 (`class AlcoveCapsuleTracker`, zero instantiations anywhere in src), and the six constants that exist only to feed it: ALCOVE_CAPSULE_ALPHA_THRESHOLD (125), ALCOVE_CAPSULE_MAX_BAND_FACTOR (129), ALCOVE_FOLLOW_MAX_WIDTH (133), ALCOVE_MEASURE_TTL_SECONDS (136), ALCOVE_NARROW_AFTER_SECONDS (139), ALCOVE_HOLD_SECONDS (142). The last two share names with genuinely live constants in alcove_observation.py:12-13 — that duplication is the trap.

**Why.** A dead synchronous tracker duplicating the live async reducer, plus two same-named-different-value constants in two files, is exactly how the next Alcove bug gets introduced. Deleting it also removes the only place that added an outward capsule margin (2.0pt) that contradicts the 6.0pt inset we're standardizing on.

**Depends on.** ships with the bracket-inset fix so the two margin constants never coexist

**Proof.** `grep -rn AlcoveCapsuleTracker src/ tests/` returns nothing; full pytest suite green (test_alcove_observation.py, test_alcove_window_level.py in particular).

### Session-keyed origin cache  ·  _small_  ·  **ADOPT from pinsonlawrimore/sidepulse branch billy/hook-performance, finding 1**

**What.** origin.py (283 lines) has zero caching: `process_ancestry`/`process_info` spawn `/bin/ps` (origin.py:190) on every single hook invocation. Add a per-session cache keyed on session_id in front of `detect_agent_origin` (origin.py:39), invalidated on SessionStart.

**Why.** pinsonlawrimore measured 11.6ms of a 47.6ms hook — 24% — spent re-deriving an answer that never changes within a session (verified across 46 sessions / 1168 events: zero sessions with more than one origin). Median hook cost 62ms→48ms. Our package is bigger than upstream's, so our baseline is likely worse than theirs.

**Depends on.** nothing

**Proof.** Instrument with the same method: run 200 synthetic hook invocations, count `/bin/ps` spawns before (≈200) and after (≈1 per session). Assert median hook wall time drops ≥15%. Test that a second session_id gets its own detection.

### Hook send timeout 0.2s → 0.03s plus a circuit breaker that exempts terminal events  ·  _small_  ·  **ADOPT from pinsonlawrimore/sidepulse, finding 3 (including their post-measurement revision)**

**What.** ipc.py:24 `HOOK_EVENT_SEND_TIMEOUT_SECONDS = 0.2` with no breaker; exceptions in the send path are swallowed. Drop to 0.03s and add a file-sentinel breaker, but exempt Stop, StopFailure, SubagentStop, SessionEnd, SessionStart, Notification, PermissionRequest from suppression — pinsonlawrimore's own revision after measuring 6/8 timeouts under 8×900KB concurrent payloads on a healthy-but-busy server.

**Why.** A wedged or busy listener costs the full 200ms on every hook, invisibly, for as long as it stays wedged. The exemption list is the non-obvious part and maps directly onto our existing terminal-event vocabulary in collector.py and provider_adapters.py.

**Depends on.** origin cache (same hook-path change window, same benchmark harness)

**Proof.** Test with a server that accepts and never drains: non-terminal events short-circuit after the breaker trips (assert wall time <5ms), terminal events still attempt the send. Second test: 8 concurrent 900KB tool_response payloads against a healthy server do NOT trip the breaker for terminal events. Verify a Stop still reaches the collector under load — this is the one that must not regress, since completion is the whole product.

### Screen Recording preflight for the Alcove capture path  ·  _medium_

**What.** alcove_observation.py:366-413 `capture_alcove_observation` calls CGWindowListCreateImage and treats a nil image (line 386) and any exception (line 412) as an ordinary 'no reading'. There is no CGPreflightScreenCaptureAccess anywhere in src. Add a one-time preflight at Screen-Bar-enable time (or lazily on first None image), cache the result, and surface a Settings hint: 'Screen Recording permission is required for the bar to follow Alcove's capsule.'

**Why.** Without the grant the entire capsule-follow feature fails 100% of the time, silently, indistinguishable from 'Alcove has nothing to show'. The owner is deploying to a second Mac — this is precisely the machine where it will bite and produce no diagnostic.

**Depends on.** compact-mode fix (both touch the same observation pipeline)

**Proof.** On a second Mac (or after explicitly revoking and re-granting SidePulse's own Screen Recording grant): with the grant absent, Settings shows the hint and doctor.py reports a bounded finding code; with it present, the hint disappears and follow works. This is the item that also converts the INFERRED diagnosis into a VERIFIED one.

## Wave 2 — Get the hook path out of the way, and make sub-agents disappear correctly. Latency architecture plus the one lifecycle question the sweep could not answer from a grep.

### Make `sidepulse/__init__.py` lazy  ·  _small_

**What.** __init__.py eagerly imports battery, collector (3331 lines), ipc, led_status, lid_sleep, models. Convert to PEP 562 `__getattr__` lazy re-exports so importing the package costs near-nothing.

**Why.** Prerequisite for the thin hook client, and it independently cuts every CLI invocation's startup. Pure mechanical change with a large blast radius but no behavior change.

**Depends on.** wave 1

**Proof.** `python -X importtime -c 'import sidepulse'` cumulative time drops by an order of magnitude; every existing `from sidepulse import X` in tests and src still resolves (full pytest green, plus an explicit test importing each name in `__all__`).

### Thin stdlib-only `hook_client.py`  ·  _large_  ·  **PORT from pinsonlawrimore/sidepulse, finding 2 — design spike first, do not port blind**

**What.** New module: read stdin, connect to the existing HookEventServer unix socket, write, exit. No `sidepulse` package import at all. Repoint hook registrations in install.py away from `hook_entry.py`'s `from .hook import hook_log_main` path. Keep hook_entry.py as the fallback when the socket is absent.

**Why.** pinsonlawrimore measured 46.6ms→20.2ms (-57%), within 3.5ms of the bare interpreter floor. Their own doc flags open questions (ordering under concurrency, dead-server fallback) that get harder on our larger codebase, so this gets a design spike first — but it is the single largest measured win available anywhere in this sweep.

**Depends on.** lazy __init__.py

**Proof.** Benchmark harness from wave 1: median hook wall time under 25ms. Correctness gates: (a) event ordering preserved under 8 concurrent hooks (assert monotonic watermark in collector), (b) with the server down, hooks still exit 0 and the fallback path writes to the log, (c) audit/redaction pipeline still runs — assert no raw tool_response content reaches the socket that wouldn't have before.

### Verify (then fix) parent → sub-agent retirement cascade  ·  _medium_  ·  **ADOPT the concept from adamstambouli/fleet-mode; their literal fix does not port — our keyed `works` reducer is a different design from their flat status list**

**What.** `grep SessionEnd src/sidepulse/operator_state.py` returns nothing — the reducer that builds CanonicalOperatorState has no SessionEnd handling at all; SessionEnd is only handled in collector.py (lines 1030, 1060, 2629, 2766, 2938) and completions.py. Meanwhile collector.py's `stale_after_seconds` defaults to 3600.0 in three places (482, 523, 956). Determine whether a parent's Stop/SessionEnd force-retires its children's work-keys immediately, or whether sub-agents ride the generic 1-hour stale window. If the latter, add the cascade.

**Why.** adamstambouli measured exactly this bug upstream: 65 stale statuses, oldest eleven hours dead, a long-finished sub-agent revived as Working on restart. Our stale window is the same 1-hour magnitude. Rule 5 says sub-agents stay invisible — a sub-agent lit for an hour after its parent finished is a direct violation, and with 200 observed sub-agents per main agent the blast radius is not theoretical.

**Depends on.** nothing (parallel to the hook client)

**Proof.** Replay a synthetic transcript: parent spawns 50 sub-agents, 30 never emit SubagentStop, parent emits SessionEnd. Assert within one reducer tick that zero sub-agent work-keys remain live, the parent shows Done, and `_serialize_latest_state` persists nothing that a restart could revive as Working. Restart the app against that persisted state and assert the aggregate is idle.

### Wire thermal / low-power into refresh cadence  ·  _small_  ·  **ADOPT from CodexBar AdaptiveRefreshPolicyCore.swift:53-102**

**What.** render_policy.py already reads the thermal-state and low-power-mode signals for LED/animation cadence. Nothing in the capacity/network refresh path consumes them. Add a tiered background-refresh interval to refresh_policy.py: 2min if the menu was opened <5min ago, 5min if <1h, 15min if <4h, else 30min; cap at 5min while coding activity was seen in the last 5min; flat 30min override under low-power or thermal pressure.

**Why.** Reuses a signal we already read, for a purpose we currently don't serve. The coalescing/backoff machinery in refresh_policy.py and capacity_refresh.py is already better than CodexBar's — this is only the cadence policy, which is the one piece we're missing.

**Depends on.** nothing

**Proof.** Pure-function tests over the decision table (menu-recency × coding-activity × thermal) with a frozen clock; assert 30min under simulated low-power regardless of menu recency, and 2min immediately after a menu open. No network involved.

## Wave 3 — Delete CodexBar, part 1: the live numbers. Credentials, fetch, and the glance. This is the wave that makes CodexBar removable in principle — but not yet in practice.

### Keychain reader: no-UI, single hardened entry point  ·  _large_  ·  **PORT-the-approach from CodexBar ClaudeOAuth/{KeychainSecurity,KeychainNoUIQuery,ClaudeOAuthKeychainPreAlertGate,ClaudeCLIRateLimitGate,ClaudeCredentialRouting}.swift — no Swift is portable, the design is**

**What.** New module (PyObjC/ctypes into Security.framework) calling SecItemCopyMatching with the no-UI flags: LAContext.interactionNotAllowed plus kSecUseAuthenticationUIFail resolved via dlsym, so the OS Allow/Deny dialog can never fire from a background poll. Blocks all real access under test/CI. Plus our own explanatory alert shown once before the first-ever OS prompt, with a re-ask cooldown, and a self-imposed cooldown after the usage endpoint 429s.

**Why.** claude_quota.py:1-51 `fetch_windows()` unconditionally raises ClaudeQuotaUnavailableError, and capacity_sources.py explicitly disclaims credential/network authority. There is no path to the owner's actual quota numbers today. CodexBar spent ~20 files learning this; the pre-alert gate and the no-UI query are the two non-obvious pieces.

**Depends on.** nothing

**Proof.** With no grant: returns a typed 'not authorized' refusal, and crucially the OS dialog does NOT appear (observe via a clean login session). With grant: returns a token whose prefix routes correctly (sk-ant-oat… OAuth vs sk-ant-admin… admin key). Under pytest: hard-fails if real Keychain access is attempted. `claude auth status --json` used as a login-only probe that touches no Keychain item.

### Codex credentials: read auth.json, refresh the token  ·  _medium_  ·  **PORT from CodexBar CodexOAuthCredentials.swift / CodexTokenRefresher.swift, with endpoint/client_id independently verified**

**What.** Read `~/.codex/auth.json` (CODEX_HOME-overridable), refresh after ~8 days idle via POST to the OAuth token endpoint with grant_type=refresh_token. Verify the endpoint and client_id against the Codex CLI's own open-source code before shipping — CodexBar's values are reverse-engineered, not documented.

**Why.** Codex is plaintext-on-disk, so this is the cheap half of the credential problem and unblocks the provider the owner is actually at 100% weekly on right now.

**Depends on.** nothing (parallel to Keychain work)

**Proof.** Fetch returns a live usage payload for the owner's Codex pro account. Refresh path tested against an expired token. Assert we never write to `~/.codex/auth.json` unless a refresh genuinely succeeded, and never log the token.

### One canonical usage snapshot shape, with named extra lanes  ·  _medium_  ·  **ADOPT from CodexBar UsageFetcher.swift:3-96/143+, ClaudeScopedWeeklyLimitMapper.swift, CodexAdditionalRateLimitMapper.swift:125-129**

**What.** Model every provider into {primary, secondary, tertiary, extra_rate_windows: [(id, title, RateWindow)]} where RateWindow is {used_percent, window_minutes, resets_at, reset_description} and remaining is ALWAYS derived (100 - used_percent), never fetched or stored. Claude's tertiary is the seven_day_opus lane; Codex's extras come from the API's generic additional_rate_limits (live on this machine: id `codex-spark-weekly`, 'Codex Spark Weekly', currently 0%). Do not hardcode Opus/Sonnet as a concept.

**Why.** This is the single most reusable decision in CodexBar — one shape that ~70 providers funnel into. Our capacity_authority.py binding-lane model (MAX_BINDING_LANES=2, per-lane `bindable`, full applicability refusal reasoning at 91-178) already subsumes CodexBar's narrower duration-only binding-cap logic, so this is a mapping layer onto machinery we already have, not new math.

**Depends on.** Keychain reader + Codex credentials

**Proof.** Live: our snapshot for Codex matches CodexBar's current reading (weekly 100% used, resets 2026-08-20T03:35Z, windowMinutes 10080) and surfaces codex-spark-weekly as a named extra. For Claude, the Opus lane appears as tertiary and any 'weekly_scoped' model limit appears as '<Model> only' without double-counting the all-models aggregate. Property test: remaining is never persisted, only derived.

### The glance: usage left, time to reset, in the menu bar and dropdown  ·  _medium_

**What.** Replace claude_quota.py's raise-only stub with the real source; feed capacity_view.py (which already has the <1% guard at 123 and format_reset with DUE/UNKNOWN/UNAVAILABLE/DISPUTED/STALE/FUTURE at 506-518 — richer than CodexBar's binary toggle). Menu-bar glyph shows the binding lane's remaining; dropdown ledger shows per-provider per-lane rows with countdown.

**Why.** Two of the owner's three at-a-glance numbers. The third (estimated remaining usage time) needs history, which is wave 4 — ship the two that don't.

**Depends on.** canonical snapshot shape

**Proof.** Side-by-side with CodexBar's live menu for 24 hours: same remaining percentage and same reset time for Codex weekly and Claude session/weekly, within one refresh interval. Reset countdown renders correctly across a real reset boundary.

### Threshold crossing detector, keyed (provider, account)  ·  _small_  ·  **ADOPT the edge-detection primitive from CodexBar HookTransitionDetector.swift:116-150 — NOT its HookRunner/HookRateLimiter outbound shell automation, which is orchestrator territory and out of scope**

**What.** Per-lane edge detector: track previous used-fraction and reset boundary per (provider, account, window) key; fire quota_low / quota_reached / quota_reset only on the crossing edge, re-checking against the current reading so a poll never re-fires an already-crossed rule; drop all baselines when config revision changes so the first sample after a settings edit never spuriously fires.

**Why.** The owner already has, in one CodexBar install, two distinct Claude OAuth identities plus an org-scoped bucket, two Codex account-key formats from a mid-life identity migration, and a Devin account. Provider-only keying is wrong on day one. This feeds the interrupt budget in wave 4, so it must exist first.

**Depends on.** canonical snapshot shape

**Proof.** Replay a usage series crossing 80% then polling ten more times: exactly one quota_low event. Change a threshold setting mid-series: no event fires on the next sample. Two accounts on the same provider cross independently.

## Wave 4 — Delete CodexBar, part 2: prediction, history, spend — and the interrupt budget becomes law. After this wave CodexBar comes off the login items.

### Per-provider usage history, persisted  ·  _medium_

**What.** Extend capacity_history_store.py to persist a per-(provider, account, lane) time series on every refresh. Bounded, with the existing 4MB-class ceiling discipline.

**Why.** CodexBar's own usage-history.jsonl is Codex-only in this install — 3833/3833 rows are provider=codex; Claude and Devin have zero. So its history feature cannot be leaned on, and its prediction is single-provider. This is where we beat it rather than match it.

**Depends on.** wave 3

**Proof.** After 48h of running, the store has rows for every configured provider (not just Codex). Assert bounded growth, atomic writes, and that a corrupted file degrades to 'no history' rather than crashing the refresh loop.

### Two-tier pace: linear on day one, historical when earned  ·  _medium_  ·  **PORT tier 1 from CodexBar UsagePace.swift:43-124; tier 2 is ALREADY-HAVE (capacity_forecast.py) — wire, don't write**

**What.** Tier 1 — port UsagePace.weekly: expected = elapsed/duration×100, delta vs actual, stage buckets at |delta| 2/6/12, plus etaSeconds when the current rate exhausts the remainder before reset, or willLastToReset + a headroom multiplier when it doesn't. Works with zero history. Tier 2 — our existing capacity_forecast.py (MINIMUM_COMPLETE_CYCLES=5, MINIMUM_VALID_SLOPES=3, bounded median slopes, self-validating against a calibration baseline with BASELINE_NOT_BEATEN / FALSE_WARNING_REGRESSED / MISS_RATE_REGRESSED refusal codes) takes over once history is sufficient.

**Why.** Third of the owner's at-a-glance numbers: estimated remaining usage time. Tier 1 is a straight port and works immediately; tier 2 already exists and is measurably more rigorous than either reference project — it just has nothing to eat. Do not rebuild the forecaster.

**Depends on.** usage history

**Proof.** Tier 1: unit tests over the stage table with a frozen clock. Tier 2: capacity_calibration.py's existing harness must report the historical model beats the linear baseline before it's allowed to display — that's what the refusal codes are for, and they are the acceptance test. End-to-end: with a fresh install, an ETA appears same-day; after five complete cycles, the display source switches and calibration passes.

### Workday-aware weekly projection  ·  _small_  ·  **PORT from CodexBar UsagePace.swift:172-254**

**What.** Optional mode that excludes weekends from elapsed/remaining time when projecting against a weekly cap, by walking day-boundary slices.

**Why.** Zero occurrences of workday/business-day/weekday anywhere in our forecast code. For a Mon–Fri operator on Codex's weekly cap, a straight-line projection is wrong by ~40% of the window. Small, self-contained, real accuracy gain.

**Depends on.** two-tier pace

**Proof.** Given a Monday-start weekly window and a Saturday 'now', workday mode reports lower expected-used than calendar mode by exactly the excluded weekend fraction. Off by default; a settings toggle flips it.

### Interrupt budget, enforced in one place  ·  _medium_  ·  **ALREADY-HAVE the routing system (interruption_policy.py) — this is wiring plus enforcement tests, not new machinery**

**What.** Route quota events through interruption_policy.py's existing action-required / important-outcome / courtesy classification. Blocked agents and critical states (5% battery) blink until dealt with; usage crossings, weather, and messages blink twice and return to normal. Nothing above 2Hz. Colorblind-safe pairings only. Sub-agents never own a light, row, or interrupt.

**Why.** The owner's law. It cannot be enforced per-feature; it has to be one gate every signal passes through, or wave 5's remote agents and wave 6's providers will each reinvent their own escalation.

**Depends on.** threshold detector (wave 3), pace (this wave)

**Proof.** Table-driven test asserting, for every signal kind: persistence (until-acknowledged vs N bursts), frequency ≤2.0Hz, and colorblind-safe pairing. A sub-agent event of any kind produces zero interrupts. Battery at 5% keeps blinking across a render-cadence change. Usage-low blinks exactly twice then returns to the normal program.

### Settings: usage history and estimated spend  ·  _medium_

**What.** Settings panes per the agreed IA (Agents / Messages / Extras + hardware + Screen Bar): usage history chart per provider, estimated spend over a configurable window, currency, and per-provider refresh cadence.

**Why.** The explicit precondition for deleting CodexBar — the owner named usage history and estimated spend as the things he needs in settings, not at a glance.

**Depends on.** usage history

**Proof.** Spend figures reconcile with CodexBar's 30-day dashboard for Codex within rounding, for the same period.

### Retire CodexBar from this machine  ·  _small_

**What.** Owner-executed: unregister its SMAppService login item and remove the app. Our side: a short checklist in docs confirming every CodexBar-derived number now has a home in JR-BAR.

**Why.** The stated goal. Naming it as a deliverable is what forces waves 3–4 to actually be complete rather than approximately complete.

**Depends on.** everything in waves 3 and 4

**Proof.** One week with CodexBar quit: the owner never needs to relaunch it. That's the only acceptance test that counts.

## Wave 5 — Two Macs, one picture — plus cloud agents. The distributed wave.

### Tailscale peer discovery + SSH state fetch  ·  _large_  ·  **PORT-the-approach from CodexBar RemoteSessionFetcher.swift:23-291 and T3 packages/tailscale/src/tailscale.ts:26-49 — Python implementation, both credited**

**What.** Shell out to the user's own `tailscale status --json`, filter to online macOS/Linux peers excluding self by DNS-label match; for each host run `ssh -o BatchMode=yes -o ConnectTimeout=3 <host> sh -lc '<our-cli> --json-v2 || <our-cli> --json'` with `||`-chained protocol fallback. Sanitize hostnames against control chars and leading dashes, shell-quote everything. Never log raw stderr from tailscale — it can contain tskey-… auth keys and node names.

**Why.** We have zero cross-device mechanism today: ipc.py is local-unix-socket-only, capacity_history_store.py is local-JSON-only. CodexBar (RemoteSessionFetcher.swift) and T3 (packages/tailscale + packages/ssh) independently converged on this exact answer — two codebases landing on the same design is a stronger signal than either alone. And it's Linux-compatible, unlike CloudKit, which would foreclose the roadmap's 'Linux later'.

**Depends on.** wave 4 (so remote rows carry real capacity data too)

**Proof.** With both Macs running, machine A's ledger shows machine B's agents with correct states, and vice versa, within one refresh. Adversarial tests: a hostname containing `;` or a leading `-` is rejected, not executed; an unreachable peer degrades to a 'stale' row rather than blocking the refresh; stderr containing a tskey- string never appears in any log.

### Remote agents in the ledger, muted in the interrupt budget by default  ·  _medium_

**What.** Remote rows render in the dropdown ledger and count toward the aggregate, but by default do not blink the local LEDs — a per-machine toggle decides whether machine B's blocked agent interrupts you at machine A.

**Why.** Surfaces have different jobs (rule 3). The ledger is the ledger — it should show everything. The LEDs are peripheral attention for the machine you're sitting at, and doubling the interrupt sources without a policy would blow the budget instantly.

**Depends on.** Tailscale/SSH fetch, interrupt budget (wave 4)

**Proof.** Machine B goes blocked: machine A's dropdown shows it, machine A's LEDs stay calm with the toggle off and escalate with it on. Sub-agents on the remote machine are absent from the payload entirely, not filtered at the receiver.

### Loopback HTTP ingest for cloud agents  ·  _medium_  ·  **ADOPT the API shape from leog/ai-pulse (credited); it is credited as SidePulse-inspired in turn**

**What.** A localhost-bound HTTP ingest (127.0.0.1, Keychain-stored bearer token, POST /v1/agents/upsert) as a second ingest surface alongside the unix socket, for agents that can't write to a mounted filesystem or be SSH'd into.

**Why.** Already flagged in our own docs/BUILD-SPEC.md item 6 from leog/ai-pulse. SSH covers the second Mac; it does not cover a cloud agent that can only make outbound calls. Different problem, different answer.

**Depends on.** nothing structural — can land in parallel with SSH

**Proof.** A curl from a container posts a status and it appears in the ledger. Security tests: binds loopback only (assert connection refused from another host), rejects a missing/wrong bearer token, and rate-limits.

## Wave 6 — Coverage, distribution, and the polish that makes it feel like a product someone else could install.

### Antigravity provider  ·  _medium_  ·  **PORT from CoolColby23/sidepulse PR #7 (credit)**

**What.** New provider in providers.py / install.py following the existing 8-provider pattern. CoolColby23 already reverse-engineered the camelCase PreInvocation/PostInvocation/Stop payload shape and validated it against a real RESOURCE_EXHAUSTED stop and a fullyIdle=false background-work stop.

**Why.** Zero occurrences of 'antigravity' in our tree — a genuine gap, and the only requested provider with a validated payload shape already in hand.

**Depends on.** nothing

**Proof.** An Antigravity session shows Working → Done in the ledger; the RESOURCE_EXHAUSTED stop maps to blocked, and fullyIdle=false does NOT close the work.

### Isolated venv installer  ·  _small_  ·  **PORT from CoolColby23/sidepulse PR #9 (credit)**

**What.** An install script that creates a dedicated venv rather than the README's current `pip install --user --break-system-packages -e .` plus manual `ln -sf`.

**Why.** Answers upstream issue #2, already named in our own docs/BUILD-SPEC.md as worth adopting, and it is the single biggest friction point for putting this on the second Mac.

**Depends on.** nothing

**Proof.** On a clean second Mac, one command installs and the app launches with hooks registered — no --break-system-packages, no manual symlink.

### Status-only privacy tier, per provider  ·  _medium_  ·  **ADOPT from djmango PR #11 (credit); generalized rather than copied**

**What.** A per-provider option that publishes only working|done|ask|blocked|idle transitions and never a message, prompt, or transcript excerpt — as a general per-provider setting, not a Cursor-specific alternative implementation.

**Why.** djmango contributed this unprompted as a privacy tier, with a test asserting no message field is ever emitted. Our Cursor provider is the full-content design; the tiered option itself is what's missing, and our own BUILD-SPEC flags 'someone contributing a privacy-tiered design unprompted' as a strong signal.

**Depends on.** nothing

**Proof.** With the tier on for a provider, a property test over 1000 synthetic events asserts no field outside the status enum and timestamps ever reaches the log or socket. The ledger still shows correct states.

### Worst-first ledger with a collapsed healthy tail  ·  _small_  ·  **ADOPT from CodexBar AccountMenuLayoutPlanner.swift:10-70**

**What.** Once a provider has ≥4 account rows, sort inactive rows most-constrained-first and fold a healthy tail behind one 'N more healthy' row when ≥2 would be hidden.

**Why.** Our menu rows sort by static keys (provider_id, lane.key, interval.days) with no severity ordering and no progressive disclosure anywhere in src. Fine at today's scale; not fine once two Macs × multiple accounts × named extra lanes all have rows.

**Depends on.** wave 5 (which is what creates the row explosion)

**Proof.** Pure-function tests: 3 rows render flat; 6 rows with 4 healthy render worst-first with a single summary row; the best switch-to candidate is flagged.

### Differentiate-without-color render audit  ·  _small_

**What.** We already observe all four NSWorkspaceAccessibilityDisplayOptionsDidChangeNotification flags in accessibility_display.py. Verify that each surface actually renders a non-color differentiator (glyph shape, badge, ring, static fallback) when differentiateWithoutColor or reduceMotion is set — menu-bar glyph, Screen Bar, and LED programs each separately.

**Why.** The signal being wired up is not the same as the surfaces reacting. Rule 6 (colorblind-safe) is only satisfied if it's true at the pixel, and leog/ai-pulse ships shape/badge/ring differentiation plus a Reduce Motion static fallback as table stakes.

**Depends on.** nothing

**Proof.** Toggle each flag in System Settings; screenshot each of the three surfaces; assert a shape/badge change, not only a hue change. Reduce Motion produces a static LED program.

### Distribution: Sparkle + EdDSA appcast, SMAppService login item  ·  _medium_  ·  **ADOPT from CodexBar's distribution setup**

**What.** Sparkle with an EdDSA-signed appcast.xml on a public raw URL for updates; SMAppService.mainApp for login-item registration (verified live via sfltool dumpbtm on CodexBar). Bake build provenance keys (git commit, build timestamp) into Info.plist.

**Why.** Well-trodden and low-friction; the alternative is hand-rolling an updater. The provenance keys are the reason this sweep could tell exactly which CodexBar build was running — worth copying for our own future debugging.

**Depends on.** isolated installer

**Proof.** A second Mac installs from the appcast and self-updates to a newer build; the login item shows in sfltool dumpbtm; the About pane reports the exact commit.

### Onboarding copy in Alcove's register  ·  _small_  ·  **ADOPT the copy pattern from Alcove's shipped Localizable.strings (reference only, no code)**

**What.** Intro → per-permission grant screens → done. One sentence per permission, in the pattern '<AppName> needs your permission to <specific verb-phrase>.' Native SwiftUI-equivalent text and SF Symbols; no illustrations.

**Why.** We are about to ask for Screen Recording (wave 1), possibly Keychain (wave 3), and Accessibility. Alcove's shipped strings are a proven, terse model the owner already accepted on this machine, and copying the register costs nothing.

**Depends on.** Screen Recording preflight (wave 1)

**Proof.** A clean-install run reaches a working state with each permission explained before its OS dialog appears, and no dialog fires without our explanation preceding it.

## Wave 7 — Owner's call — each independently shippable, none load-bearing. Listed so they're decisions, not oversights.

### Audio-reactive LED visualizer  ·  _medium_  ·  **PORT from CoolColby23/sidepulse branch local/mac-extras (credit — unsubmitted personal branch)**

**What.** A Swift helper using ScreenCaptureKit audio-only SCStream streams RMS at ~80ms; Python converts to a perceptually-curved level with asymmetric attack/decay (45ms rise / 300ms fall) and renders a green→red 8-LED VU meter. Comes with a battery-threshold pulsing-red alert at 30/20/15/10%.

**Why.** The most novel idea in the whole sweep, and it is fun. But: SCStream audio capture requires Screen Recording permission even with no video, and it is unproven outside one person's local branch — never even proposed upstream. It also competes with the LEDs' actual job (peripheral 'does anything want me'), so it can only ever be a mode you switch into, never a default.

**Depends on.** Screen Recording preflight (wave 1), interrupt budget (wave 4)

**Proof.** Music playing produces a meter that responds within ~100ms; the mode is opt-in, exits cleanly back to agent status, and never preempts a blocked-agent or critical-battery signal.

### Burn-down widget  ·  _large_  ·  **ADOPT the App-Group-snapshot handoff architecture from CodexBar CodexBarWidget + WidgetSnapshot.swift**

**What.** A WidgetKit extension fed by a JSON snapshot the main app writes into a shared App Group container, with bounded load/save timeouts and a circuit breaker that disables container I/O for the process after a timeout.

**Why.** CodexBar's widget is literally named BurnDown and shows used% vs expected pace outside the app. Whether the owner wants a widget at all is unknown — the menu bar may be enough.

**Depends on.** wave 4

**Proof.** The widget shows the same remaining/reset as the menu bar and survives the main app being quit (stale-but-labeled, not blank).

### Kiro provider  ·  _large_

**What.** Original reverse-engineering of Kiro's hook/event surface.

**Why.** Upstream issue #3, open, zero comments, zero reactions. Unlike every other requested provider, nobody anywhere in this ecosystem has built one — zero mentions across all 9 forks and our tree. No shortcut exists.

**Depends on.** nothing

**Proof.** A Kiro session shows Working → Done in the ledger.

### T3Code provider  ·  _medium_

**What.** The unaddressed half of upstream issue #12.

**Why.** Zero occurrences of t3code/t3chat anywhere in our source or any fork. Worth noting T3's own adapters just relabel and forward the CLI's native account/rateLimits/rate_limit_event protocol messages — so if we build this, check for native rate-limit telemetry before writing a scraper.

**Depends on.** nothing

**Proof.** A T3Code session appears in the ledger with correct states.

## Wave 1, in implementation detail

WAVE 1 — "Stop lying, stop stalling". Repo root: /Users/jonathanreed/Documents/Codex/2026-08-12/hey-so-i-was-working-on/work/sidepulse-manager-completion (call it $REPO; source at $REPO/src/sidepulse, tests at $REPO/tests). Every line number below was re-verified against the working tree today. Seven changes, four of them one-file-one-function. Suggested order: 4 (delete) → 2 → 3 → 7 (Alcove cluster), then 1 (installer), then 5 → 6 (hook path, shared benchmark).

--- 1. Sleep-prevention installer ---
File: $REPO/src/sidepulse/status_bar.py
Function: `open_terminal_setup_command(command: str, *, filename: str = "install-sleep-helper.command") -> Path` at line 14635. It builds a zsh script, writes it to `default_state_dir()`, chmods 0700, then `subprocess.Popen([trusted_system_tool('open'), str(script_path)], ...)`.
Change: add a terminal resolver alongside it — reuse the reviewed-absolute-path approach already in $REPO/src/sidepulse/status_bar_launch.py (`GHOSTTY_APPLICATION_PATHS`): resolve, in order, a user-configured terminal setting, then /System/Applications/Utilities/Terminal.app, then known-good alternates from an allowlist. Invoke `open -a <resolved absolute .app path> <script_path>`. Do not consult LaunchServices for the `.command` UTI at all. Second-choice implementation, equally acceptable and simpler: skip `open` entirely and exec the script directly — it is already chmod 0700 and self-contained — but then the user loses the visible window, so prefer `open -a`.
Caller: status_bar.py:7710-7712 currently does `path = open_terminal_setup_command(...)` then `messages.append(f"Sleep prevention installer opened: {path}")`. Change the message to state that a terminal window was opened and the install is not complete until confirmed, and re-poll `sleep_helper_installed()` on the next `refresh_setup_window()` so the checkbox reflects reality rather than the Popen not raising.
Test: new $REPO/tests/test_terminal_launch.py case (file exists) — inject a fake launcher, assert argv contains `-a` and an absolute path ending in `.app`; assert the function raises rather than silently succeeding when no terminal resolves.
Credit in commit: seanhellwig/sidepulse.

--- 2. Alcove bracket inset ---
File: $REPO/src/sidepulse/virtual_device.py
Line 2733, inside `_apply_latest_alcove_observation`-fed block: `follow_width = observation.width` → `follow_width = observation.width + 2.0 * ALCOVE_ACCENT_EDGE_INSET`.
Constant: ALCOVE_ACCENT_EDGE_INSET = 6.0 at virtual_device.py:155, with the comment explaining why the draw path insets ("Core Graphics clips bloom and stroke antialiasing at a window edge"). Use that same constant — do not introduce a second margin value.
Downstream, unchanged: `follow_width` becomes `alcove_total_width` into `virtual_window_frame_for_screen` (~line 2754); `_draw_wings_only` (line 1527) calls `alcove_accent_horizontal_bounds(width)` (line 1540) which applies the 6.0 inset; `_alcove_body_path` (line 1070) recenters via `x_offset = max(0, (width - observed_width) / 2)` and needs no change.
Test: add to $REPO/tests/test_alcove_observation.py — a pure assertion that the follow width exceeds the observation width by exactly 2×ALCOVE_ACCENT_EDGE_INSET, and that the resulting drawn stroke bounds equal the observation bounds.
Ground truth for the visual check on this Mac: hardware notch 185×32pt at x [663.5, 848.5]; Alcove's own opaque backdrop ~190pt at x [661, 851] (it over-paints by ~2.5pt/side deliberately); Alcove's CGWindow hit-region is 624×320 at (444,0) and is NOT a sizing reference — that path was tried twice and is a dead end. Alcove's idle glow breathes and is asymmetric, so compare against the alpha-scanned contour, never a single screenshot.

--- 3. Compact mode capsule follow ---
File: same, virtual_device.py. Two edits.
(a) Line 2673: the guard is `if (wings_only and wing_override is None and getattr(self, "follow_alcove_width", True)):`, where `wings_only = alcove_active and self.wraps_menu_bar` (2640) and `compact = alcove_active and not self.wraps_menu_bar` (2641). Change the first conjunct from `wings_only` to `alcove_active`. This makes the observation pipeline (AlcoveCaptureRequest → AlcoveObservationBuffer → reducer) run in compact mode too, so `follow_observation` / `follow_width` / `follow_center_x` stop being permanently None, and the existing `self.view.setAlcoveSilhouette_(...)` call around 2780-2791 starts populating.
(b) `_draw_compact_accent` at virtual_device.py:1800 currently derives everything from `notch_width, wing_offset = self._notch_geometry()` (hardware slot). Add: when `self.alcove_silhouette is not None`, size and center the accent line from the observed capsule width/center instead — mirror how `_draw_wings_only` consumes the silhouette via `_alcove_body_path`. Keep the existing per-column `glow_color_for_column` loop and COMPACT_ACCENT_HEIGHT; only the width/center inputs change.
Test: feed a synthetic AlcoveObservation (width 190, center_x 756) through the compact path with `wraps_menu_bar = False`; assert the drawn accent spans the observed capsule, not the 185pt hardware notch. Second test: with `alcove_active = False`, compact still falls back to hardware geometry.

--- 4. Delete AlcoveCapsuleTracker ---
File: same. Delete, in this order (bottom-up to keep line numbers stable):
  - lines 545-620: `class AlcoveCapsuleTracker` (its `desired_total_width()` at ~588 does `reading + 2.0 * ALCOVE_CAPSULE_MARGIN`; that 2.0pt margin is the wrong value and contradicts the 6.0pt fix above — this is why it goes out in the same wave).
  - lines 539-543: `measured_alcove_capsule_width()`, an explicit dead stub whose own docstring says Alcove capture now exists only in the serial worker and which always returns None.
  - the six now-orphaned constants at lines 125, 129, 133, 136, 139, 142: ALCOVE_CAPSULE_ALPHA_THRESHOLD, ALCOVE_CAPSULE_MAX_BAND_FACTOR, ALCOVE_FOLLOW_MAX_WIDTH, ALCOVE_MEASURE_TTL_SECONDS, ALCOVE_NARROW_AFTER_SECONDS, ALCOVE_HOLD_SECONDS. The last two share names with live-and-different constants in $REPO/src/sidepulse/alcove_observation.py:12-13 — that shadowing is the actual hazard.
Verify before deleting: `grep -rn "AlcoveCapsuleTracker\|measured_alcove_capsule_width\|ALCOVE_CAPSULE_ALPHA_THRESHOLD\|ALCOVE_CAPSULE_MAX_BAND_FACTOR\|ALCOVE_FOLLOW_MAX_WIDTH\|ALCOVE_MEASURE_TTL_SECONDS" $REPO/src $REPO/tests` — the only hits should be the definitions themselves.
Do NOT touch `alcove_window_level()` at virtual_device.py:425-451. Re-measured live this session: Alcove's two on-screen windows sit at CGWindowLayer 2147483629 and 2147483628, max+1 = 2147483630, which equals the ABOVE_ALCOVE_WINDOW_LEVEL fallback. The function already measures at runtime with a safe floor and is wired in at line 2767. It is correct. The roadmap note calling it hardcoded is stale.

--- 5. Session-keyed origin cache ---
File: $REPO/src/sidepulse/origin.py (283 lines, zero caching today).
Hot path: `detect_agent_origin` (line 39) → `origin_from_processes` (102) → `process_ancestry(pid, limit=10)` (171) → `process_info(pid)` (187), which spawns `/bin/ps -p <pid> -o pid= -o ppid= -o comm= -o command=` (line 190) once per ancestor, per hook.
Change: add a module-level dict keyed on session_id holding the resolved AgentOrigin, populated on first detection and cleared on SessionStart. Guard size (bounded, LRU or capped dict) so a long-lived listener can't grow it unbounded. Keep `detect_agent_origin`'s signature; add an optional `session_id` param and fall through to the uncached path when it's absent.
Evidence for safety: pinsonlawrimore verified across 46 sessions / 1168 events that no session ever produced more than one origin.
Test: 200 synthetic hook invocations across 3 session_ids; assert `/bin/ps` spawn count drops from ~200 to ~3 (patch subprocess and count). Assert a new session_id triggers a fresh detection. Assert SessionStart clears the entry.

--- 6. Hook send timeout + circuit breaker ---
File: $REPO/src/sidepulse/ipc.py. `HOOK_EVENT_SEND_TIMEOUT_SECONDS = 0.2` at line 24; used as the default for the send helper at line 404 and `send_hook_event` at 438-443.
Change (a): 0.2 → 0.03.
Change (b): file-sentinel circuit breaker — after N consecutive timeouts, write a sentinel into the state dir and short-circuit subsequent sends until it ages out or a send succeeds. Exempt these event names from suppression, always attempting the send: Stop, StopFailure, SubagentStop, SessionEnd, SessionStart, Notification, PermissionRequest. This exemption list is pinsonlawrimore's own revision after measuring 6/8 timeouts under 8×900KB concurrent payloads against a healthy-but-busy server — without it, a busy machine trips the breaker and drops completions, which is the one failure this product cannot have.
Note: the send path currently swallows exceptions. Keep that (hooks must never fail the agent), but increment a counter the breaker and doctor.py can read, so "we are dropping events" becomes observable rather than invisible.
Test: (i) server accepts and never drains — non-exempt events short-circuit in <5ms once the breaker trips; (ii) exempt events still attempt; (iii) 8 concurrent 900KB tool_response payloads against a healthy server do not trip the breaker for exempt events; (iv) a Stop reaches the collector under that load — assert via $REPO/tests/test_canonical_ipc_reconciliation.py-style reconciliation, not just a socket write.
Shared harness: write one small benchmark script (scratchpad or $REPO/scripts) that times N hook invocations end-to-end and reports median. Items 5 and 6 both report against it, and it becomes the acceptance instrument for wave 2's thin hook client.

--- 7. Screen Recording preflight ---
File: $REPO/src/sidepulse/alcove_observation.py. `capture_alcove_observation()` at 366-413 calls Quartz.CGWindowListCreateImage; a nil image is handled as a normal miss at 386-387 and a bare `except Exception: return None` at 412-413 swallows everything else. `grep -rn "ScreenCaptureAccess\|CGPreflightScreenCapture\|CGRequestScreenCapture" $REPO/src` returns nothing.
Change: call `CGPreflightScreenCaptureAccess()` once when the Screen Bar / Alcove-follow feature is enabled, cache the result, and re-check lazily after the first None image. Expose the state through doctor.py as a bounded, content-free finding code (matching its existing enum discipline), and surface a Settings hint. Do not call `CGRequestScreenCaptureAccess()` silently — per wave 6's onboarding pattern, explain first, then prompt.
Note this is the one wave-1 item whose diagnosis is INFERRED rather than measured (verifying it required revoking a live TCC grant, out of scope for the read-only sweep). The test below is what converts it.
Test: on the second Mac, or after explicitly revoking and re-granting SidePulse's Screen Recording permission — with the grant absent, the Settings hint appears and doctor reports the code; with it present, follow works and the hint clears. Also assert the missing-grant state is distinguishable in logs from "Alcove is running but has nothing to show".

## Deletions

CODE DELETED IN WAVE 1
- virtual_device.py:545-620 — `class AlcoveCapsuleTracker`, an older synchronous capsule tracker duplicating the live async AlcoveObservationReducer. Zero instantiations in src.
- virtual_device.py:539-543 — `measured_alcove_capsule_width()`, a stub that always returns None; the only input the dead tracker had.
- virtual_device.py:125, 129, 133, 136, 139, 142 — six constants that exist only to feed the dead tracker. ALCOVE_NARROW_AFTER_SECONDS and ALCOVE_HOLD_SECONDS additionally shadow live, differently-valued constants in alcove_observation.py:12-13.
- The bare-`open` LaunchServices hand-off in status_bar.py:14635, and the success message at 7710-7712 that reports an installation from a Popen that merely didn't raise.

CODE REPLACED IN LATER WAVES
- Wave 2: eager imports in sidepulse/__init__.py (battery, collector, ipc, led_status, lid_sleep, models) → lazy PEP 562 re-exports. hook_entry.py's `from .hook import hook_log_main` becomes the fallback, not the primary path, once hook_client.py lands.
- Wave 3: claude_quota.py `fetch_windows()`'s unconditional `raise ClaudeQuotaUnavailableError(CLAUDE_REMOTE_QUOTA_UNSUPPORTED)` — replaced by a real source. capacity_sources.py's "no credential, filesystem, or network authority" disclaimer stays true of that module; the authority moves to a new, explicitly-scoped credential module rather than being smeared into the pure layer.
- Wave 6: the README's `python3 -m pip install --user --break-system-packages -e .` plus manual `ln -sf ~/.local/bin` instructions → the isolated venv installer.
- Wave 4: CodexBar.app itself, and its SMAppService login item ('2.com.steipete.codexbar' and '8192.com.steipete.codexbar', both live in sfltool dumpbtm today). That's the actual deletion this plan exists to earn.

DELIBERATELY NOT BUILT — decided, not overlooked
- CodexBar's ClaudeWeb cookie-scraping fallback (Providers/Claude/ClaudeWeb/). Already excluded by our own written policy in docs/superpowers/specs/2026-08-13-...-capacity-plane-design.md:121-135 (no browser cookies, web storage, private endpoints, DOM scraping, or session replay). CodexBar uses it as a backstop; we won't.
- CloudKit config sync. Apple-only, would foreclose the "Linux later" roadmap. SSH+Tailscale is the answer instead — and CodexBar itself built that too, so we're not even diverging from it.
- CodexBar's HookRule/HookRunner/HookRateLimiter outbound shell-command automation. That's orchestrator territory; the owner's stance is explicit. We take only the edge-detection primitive from HookTransitionDetector.
- CodexBar's QuickJS/TypeScript sandboxed provider plugin system. Genuinely a capability we lack, but it's a security-sensitive interpreter-sandbox project with no urgency at 8 first-party harnesses.
- i18n (CodexBar ships ~20-25 languages). Irrelevant for a personal single-user menu-bar tool.
- Rebuilding capacity_forecast.py. Verified directly against source: it requires 5 complete cycles and 3 valid slopes, computes bounded median-based burn rates, and self-validates against a calibration baseline with explicit regression refusal codes (BASELINE_NOT_BEATEN, FALSE_WARNING_REGRESSED, MISS_RATE_REGRESSED) across a ~35-value closed refusal taxonomy. CodexBar's UsagePace.swift is a single-sample linear projection. We wire ours up; we do not replace it with theirs. Port only their zero-history tier-1 estimator and the workday-aware weekly slicing.
- Rebuilding capacity_authority.py's binding-lane model. It already generalizes CodexBar's binding-cap concept (MAX_BINDING_LANES=2, per-lane `bindable`, refusal reasoning across model/feature/account/pool/auth-mode mismatches, not just window duration).
- Rebuilding refresh coalescing/backoff. refresh_policy.py + capacity_refresh.py already have per-source exponential backoff, Retry-After scheduling, menu-open staleness refresh, and COALESCED / QUEUED_FOR_COOLDOWN as first-class decisions. Only the cadence-tier policy is missing.
- Rebuilding provider abstraction. provider_contracts.py's versioned CapabilityAuthority negotiation is more formal than T3's per-driver Effect Schema or CodexBar's convention-only folders.
- Re-fixing alcove_window_level() (virtual_device.py:425-451). Already measures at runtime with a safe floor; re-measured live and correct.
- Re-fixing the Codex usage-limit phantom-Working bug (upstream issue #4 / PR #6). Already fixed in our tree, more thoroughly: collector.py's `codex_usage_limit_terminal()` recognizes the structured usage_limit_exceeded error and emits a synthetic StopFailure from a task_complete transcript record with no live Stop hook, wired through operator_state.py's `_codex_usage_limit_can_close_direct_work`.
- Re-adding Cursor / Hermes / OpenCode / Devin / OpenClaw providers. All already in providers.py / install.py, matching or exceeding every community PR.
- Re-suppressing subagent Ask bands. settings.py already has `subagent_asks_alert: bool = False` wired through attention.py — off by default and user-configurable, better than adamstambouli's hardcoded version.
- CoolColby23's diagnostics ZIP with log tails (PR #10). Our doctor.py is deliberately content-free with fixed enum codes and counts. Two philosophies; we keep ours. Recorded as a decision, not a gap.

## Still unknown — needs a decision

1. SCREEN RECORDING — the wave-1 preflight assumes CGWindowListCreateImage silently returns nil without the grant. That follows from documented macOS behavior and the total absence of preflight code, but was not reproduced (reproducing it meant revoking a live TCC grant on your machine). Confirm on the second Mac. Related and bigger: are you willing to grant Screen Recording at all for capsule-follow? If not, wave 1 item 7 becomes "detect and degrade gracefully to hardware geometry, permanently" and the wave-7 audio visualizer is dead on arrival, since SCStream audio-only needs the same grant.

2. CREDENTIALS — wave 3 reads your own Claude OAuth token out of the login Keychain and your Codex token out of ~/.codex/auth.json. Explicit yes/no needed before any of it is written. Also: CodexBar's credential facts are reverse-engineered, not documented — the sk-ant-oat… / sk-ant-admin… prefix routing, and the Codex refresh endpoint plus client_id app_EMoamEEZ73f0CkXaXp7hrann. Do you want these verified against the Codex CLI's own open-source code before we ship, or is matching CodexBar's live-working behavior good enough?

3. CLAUDE'S THIRD LANE — CodexBar's ClaudeProviderDescriptor.swift:105 sets `opusLabel: "Sonnet"` while sourcing the tertiary window from the API's seven_day_opus field. Could not resolve from static source why. One screenshot of CodexBar's live Claude card on your machine settles what that lane is actually labeled for your plan tier, and we copy it.

4. INTERRUPT BUDGET, EXACT NUMBERS — "blink a couple of times" needs to be a constant. How many bursts (2? 3?), at what rate under the 2Hz ceiling, and at what usage thresholds do we fire at all (80%? 90%? both, escalating)? And does a usage crossing on a provider you aren't currently using still get its bursts, or only the one you're actively burning?

5. SECOND MAC — is Tailscale already installed and logged in on both, and are SSH keys set up between them with BatchMode working? Wave 5 assumes yes; if not, that's setup work that belongs in the wave. Also: on-demand poll only when the menu opens, or continuous background sync? CodexBar's model is on-demand, which is cheaper and simpler.

6. CLOUD AGENTS — you mentioned running agents in the cloud. Which harness, and where? If they can be SSH'd into, wave 5's Tailscale path covers them and the loopback HTTP ingest can be dropped. If they can only make outbound calls, the ingest is required and should move earlier in wave 5.

7. REMOTE INTERRUPTS — default for wave 5: machine B's blocked agent shows in machine A's ledger but does NOT blink machine A's LEDs. Is that right, or do you want a blocked agent anywhere to reach you everywhere?

8. WIDGET — do you want a desktop/Notification-Centre burn-down widget at all (wave 7), or is menu bar + dropdown sufficient? It's a large item and it only earns its place if you'd actually look at it.

9. KIRO AND T3CODE — Kiro (upstream issue #3) has zero prior art anywhere; nobody in the ecosystem has built one, so it's full reverse-engineering. T3Code (issue #12's other half) is likewise unaddressed everywhere. Do you personally use either? If not, drop both from wave 7 rather than carrying them.

10. AUDIO VISUALIZER — genuinely the most fun idea found, and it conflicts with the LEDs' actual job. Confirm it can only ever be a mode you deliberately switch into, never something that preempts a blocked agent or a 5% battery.

11. MEMORY CORRECTION — the CodexBar reference clone is NOT at .../sidepulse-manager-completion/work/reference-sources/CodexBar as recorded; it's one level up at /Users/jonathanreed/Documents/Codex/2026-08-12/hey-so-i-was-working-on/work/reference-sources/CodexBar (that directory under sidepulse-manager-completion contains only t3code). Also that clone is shallow (depth 1) pinned at c4ed34d0, while the running CodexBar binary reports commit bff43f8a2c — ~1 day apart, ancestry unprovable. Want me to update memory and/or deepen the clone?

12. ALCOVE'S HELPER BUNDLE ID — Alcove's login-item XPC service (Contents/XPCServices/AlcoveHelper.xpc) ships with CFBundleIdentifier `com.apple.controlcenter.AlcoveHelper`, an Apple-namespaced id on entirely third-party, non-sandboxed code. Stated as observed fact, not an accusation — I did not investigate intent. Worth asking Alcove's author rather than either of us guessing. Flagging because we coexist with this process and are about to depend on it more.
