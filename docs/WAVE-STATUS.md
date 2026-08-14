# Wave status — measured, not asserted

Updated 2026-08-14. Every number here came from the running machine or the
test suite, not from reading code.

---

## Wave 1 — shipped (commits 93f9090, 1648c32)

The app was deaf: every hook event since the 0.2.1 install died in a
`TypeError` (8.5 MB of identical tracebacks, `latest.json` frozen for an
hour). Installed hooks spoke the pre-hint wire format. Also fixed: the
crowd never rendered, presets stomped layout, and the LED write storm.

Live after install: both devices found, **0 error bytes in 45 s**, **0 LED
rewrites in 45 s**, RSS 768 → 337 MB.

## Wave 2 — shipped (commits 8a37b27, 4548d82)

**The scan cache was the app's largest resident object.** 18.2 MB holding
211k records and 90 days of history, parsed into Python objects on every
scan, for a graph window defaulting to 7 days. Now bounded by retention,
a write budget, and a read ceiling; the dedupe table (41% of the file, one
64-char HMAC per usage event) is truncated to 128 bits.

| | before | after |
|---|---|---|
| cache on disk | 18.2 MB | 4.90 MB |
| **parse cost, every 5 min** | **139 MB** | **53 MB** |
| records / dedupes | 211,743 / 99,475 | 74,269 / 34,487 |

Retention introduces a failure mode and pays for it: a truncated entry still
matches its file on `(mtime, size, device, inode)`, so entries now record the
floor they were truncated to and are refused when the window widens. Caught
by its own test before it shipped.

**The package `__init__` was eager**, and the hottest importer is
`hook_entry` — a short-lived process that runs on every hook event and needs
none of `battery`, `collector`, `led_status` or `lid_sleep`.

    import sidepulse.hook_entry    120.7 ms -> 5.5 ms

**One reaped sub-agent muted its parent forever.** `track_completions` is fed
the full timeline (live *and* stale) on purpose, to fix an earlier
missed-celebration bug — so a worker that is killed or crashes never emits a
terminal event, stays non-COMPLETED for the life of the process, and holds
its parent's completion open permanently. At 100+ workers per parent, at
least one dying without a terminal event is the expected case. A strong
candidate for why completions felt unreliable.

Thermal and Low Power Mode were **already** wired end to end (verified live:
`thermalState 0`, cadence stepping 60/45/15/8 fps). No change needed.

## Wave 2.5 — the log treadmill (found while cleaning up)

Three state logs sat at **exactly 4,000 lines** and 23.1 / 10.7 / 7.8 MB.
Exactly the line cap, and far above the 5 MB byte threshold that triggers a
trim — which means compaction re-ran on **every hook write**: read the whole
file, rebuild it, atomic replace, fsync. ~46 MB of I/O per event for codex.

Cause: the cap was a line *count*, and records range from ~500 bytes
(claude) to ~5,800 (codex), so 4,000 lines is anywhere from 1 MB to 22 MB.
The post-trim size is now bounded too, strictly below the trigger, so one
compaction actually ends the need to compact.

Two more pieces of machinery existed and had **zero callers**:
`trim_oversized_logs` (imported into `status_bar` and never invoked — which
is why a provider that went quiet left its log frozen oversized forever),
and a 17.7 MB `usage-debug-cache.json` with no reader anywhere in the tree.
Both now run at launch.

## Wave 3 — partly shipped, and deliberately stopped short

Built, tested, and correct:

- **`credentials.py`** — the single hardened credential entry point. Never
  raises a Keychain dialog on a background timer; a denial earns an
  escalating cooldown that survives restarts; secrets never reach a repr, a
  log, or an error string.
- **`claude_quota.fetch_windows`** — the live read against the same OAuth
  usage endpoint Claude Code itself uses, with the same beta header. Failures
  carry reason *codes*, never response bodies. Returns every window,
  including the per-model sub-caps.
- **Threshold defaults, in `signals.py`** — the owner's 90 / 95, plus input
  coercion and the burst budget. Note the correction: I first wrote a whole
  parallel `quota_alerts.py` detector before finding that `signals.py`
  already had `quota_crossings` (upward transitions only, silent on first
  sight) *and* `quota_resets`. The duplicate was deleted; only the genuinely
  missing pieces were kept, next to the detector that already existed.

**Not shipped, on purpose.** Four independent switches each made Claude
limits unreachable, and flipping them is not the same as finishing the job:

1. `fetch_windows()` unconditionally raised — **fixed**.
2. `with_claude_plan_limits_enabled()` discarded its argument — *left*.
3. Claude declared no `remote_quota_windows` source — *left*.
4. The refresh coordinator registered Claude `enabled=False` — *left*.

Switches 2–4 are held closed by a deliberate architecture, not an oversight:
`tests/test_capacity_consumer_authority.py` asserts that a capacity reading
may only reach a consumer through `capacity_authority.select_binding_lanes`,
which refuses stale, model-inapplicable and unknown-source evidence. My first
attempt wired the detector to **raw provider percentages**, bypassing that
layer entirely — which is precisely how a false 95% ends up blinking the
hardware. That gate caught it. The switch-flips were reverted.

**What opens them honestly:** declare Claude's capacity lanes
(`5-hour`, `weekly`, and the per-model sub-caps) as `CapacityLaneDescriptor`
entries in `provider_contracts`, add `claude/quota` to `_FIRST_PARTY_ADAPTERS`,
emit `QuotaLaneObservation`s via `build_observation`, and let
`select_binding_lanes` authorise them. Codex already works exactly this way
and is the template.

### A live finding worth knowing

Claude Code's Keychain item on this machine holds `accessToken: ""` and
`expiresAt: 0`, with only a valid `refreshToken`. It mints access tokens on
demand rather than caching them.

We deliberately do **not** perform that refresh. The refresh token *rotates
on use*, so minting our own would invalidate the copy Claude Code holds and
break the `claude` login — trading a status readout for the actual tooling.
The state is detected and named (`claude_remote_quota_needs_sign_in`) instead.

Independent corroboration: CodexBar is installed and running here, and its
`usage-history.jsonl` contains **3,831 codex records and zero Claude
records**. It is not resolving Claude usage on this machine either.

## Waves 4–7 — not started

Usage history and spend, multi-Mac via Tailscale, cloud agent ingest, new
providers, the animation editor, Sparkle distribution.

---

## Standing rules, reaffirmed by this round

1. **Reachable or it isn't done.** Two log janitors, a 1,139-line capacity
   presentation module, and every blend mode were all written, tested, and
   never called.
2. **Test the seams.** Every bug worth finding this round was a reachability
   or process-boundary failure, invisible to thousands of green unit tests.
3. **A closed switch may be load-bearing.** Before opening one, find out what
   it was holding.
