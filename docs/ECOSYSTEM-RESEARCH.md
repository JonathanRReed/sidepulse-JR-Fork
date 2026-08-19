# Ecosystem research — upstream, forks, and T3 Code

Surveyed 2026-08-18. This file records what exists elsewhere, what was
ported, what was deliberately skipped, and what is worth building next.

Also fixed while testing the ports: the deep-work patina factor crept
continuously, defeating the phase-free LED write dedupe — the device
animation restarted (a visible blink) on every hook event once any session
had worked 15 minutes. The factor is quantized to 0.05 steps now; the
device rewrites at most once every ~3 minutes from patina.

## Ported into this fork (2026-08-18)

| Source | What | Where it landed |
| --- | --- | --- |
| upstream PR #16 (CoolColby23) | Kiro CLI provider: dedicated managed agent file (`~/.kiro/agents/sidepulse.json`, launched with `kiro-cli --agent sidepulse`), refuses unmanaged files, camelCase natives normalize to canonical events, live_agent_events only (no ask-shaped hook). Detection-gated: quiet until Kiro is installed. Colour #704028 clears the dichromacy gate by dE >= 33 (Kiro's brand purple collapses onto Codex blue under deuteranopia). | `providers.py`, `install.py`, adapters/labels/inventory, `tests/test_kiro_provider.py` |
| upstream PR #20 (d31tcjg) | A failed tool the agent continues past is Working, not Blocked — no more one amber flash per failed grep. Terminal failures (`StopFailure`, `PermissionDenied`) still block. | `_collector_legacy.mode_for_event` |
| upstream PR #21 (quinnypig) | Keep-awake battery guard: optional release of `caffeinate` while on battery; an unknown power state never releases. Off by default (historical behavior preserved), `keep_awake_on_battery` setting. Uses our async battery runtime, not a new `pmset` subprocess. | `keep_awake.py`, settings, `sync_keep_awake` |

## Checked and already covered better here

- **PR #6 / issue #4 (Codex usage-limit reached)** — our transcript pipeline
  already classifies `task_complete` usage-limit payloads as `StopFailure`
  (`codex_usage_limit_terminal`), and the capacity authority owns the quota
  story.
- **PR #14 (OpenCode + T3 Code support)** — both shipped here first, with a
  bounded read-only T3 SQLite projection and a compatibility manifest.
- **PR #11 (Cursor), #13/#5 (Hermes), #7 (Antigravity)** — all already
  first-class intake providers here.
- **Upstream `3b83293` ScriptingBridge startup fix** — we never import
  ScriptingBridge.
- **Upstream custom-terminal commits (`e30ec59`, `4721a6c`)** — our reviewed
  terminal matrix (Ghostty/iTerm/Terminal, absolute paths, no shell search)
  supersedes it.

## Worth building next (not started)

- **Screen Bar render cost** — with the bar animating and agents active the
  app sits near ~25-30% CPU; sampling shows the per-frame path is
  `JSValue callWithArguments:` marshaling into sdled.wasm through PyObjC.
  Candidates: batch several frames per JSC call, render at the panel's
  delivered rate only while visible, or precompute a cycle's frames once
  per program write. Measure against the Instruments ritual before and
  after; the budget doc wants idle-motion <= 2.5%.

- **Kiro provider (upstream PR #16)** — hooks via `~/.kiro/agents/sidepulse.json`,
  launched with `kiro-cli --agent sidepulse`. Not ported: Kiro is not
  installed on this Mac, and unreachable providers violate the reachability
  ratchet. Port the day Kiro lands here; the PR's shape maps cleanly onto our
  `providers.py` detection + `hook_entry` pattern.
- **SSH remote monitoring (upstream PR #22, mac8005)** — monitor Claude/Codex
  sessions on remote hosts over SSH. Different job from our read-only SFTP
  ledger (theirs observes remote sessions live; ours publishes a desk
  summary). Evaluate against the T3 integration first: T3 already carries
  remote machines, and we project T3 threads — remote coverage may arrive
  for free through T3 rows.

## T3 Code (pingdotgg/t3code) — provider tech to watch

- **#7419 fix(server): count every provider instance on the Usage page** —
  matches our per-instance `source_instance_id`; watch how they dedupe.
- **#5684 (merged) usage page reading provider transcripts across
  environments** — cross-machine transcript usage; adjacent to our
  cross-Mac provider sync. Their merge strategy (freshest-wins per account)
  matches our `apply_merged_sync_to_state`.
- **#7424 [codex] grouped projects on another machine** — machine profiles;
  relevant to the multi-Mac ledger's "machine" column. Checked 2026-08-18:
  the local Nightly schema (`projection_threads`/`projection_projects`) has
  NO machine or environment column yet, so surfacing "which machine" from
  T3 rows must wait for a schema bump — re-check on the next fixture
  refresh.
- **#7463 reconcile OpenCode idle state after restart** — same class of bug
  as our startup replay; compare their reconciliation on next T3 bump.
- Our compatibility manifest pins T3 **0.0.33**; T3 Nightly moves fast — the
  probe currently reads his live database fine, but re-run
  `sidepulse integrations probe t3code --json` after T3 updates and refresh
  the fixture when the schema fingerprint moves.

## T3Notch (zortos293/T3Notch) — announcer-surface ideas

An independent Alcove-style notch for T3 Code (SwiftUI, macOS 26+). Directly
relevant to the Screen Bar / announcer rung:

- **Approvals and questions answered in place**, one slide at a time — the
  announcer carrying not just the question but the ANSWER controls.
- **One card per agent, grouped by machine and project** when several run.
- **Provider logo + model + machine + branch + turn duration** on the card.
- **Activity feed** of recent commands/files, in-flight items tinted.
- **Task list from the agent's plan** with tick animation as steps land.

These fit the locked three-surface law: all of it belongs to the announcer
(words) or the browser (ledger) — none of it adds LED rungs.

## Other T3 ecosystem repos

- `13kparkin/Pivot` — T3 fork as a "remote-ready agent control surface".
- `JSvandijk/t3code-mobile` — Android companion over HTTPS/PWA proxy.
- `maria-rcks/t4code` — T3 over a Tailscale tailnet (pairs with our
  Tailscale/SFTP multi-Mac stance).

## Animation ideas (2026-08-18 brainstorm, owner to pick)

All shaped to the interrupt budget: finite bursts or ambient calm, nothing
held bright, nothing above 2Hz.

1. **Handoff baton** — when one agent completes and another starts within a
   few seconds, one bright pixel travels from the finisher's block to the
   starter's, then fades. Narrative: "Claude passed it to Codex."
2. **Firefly completion** — in multi-agent mode, when ONE agent finishes,
   only its LEDs flicker-fade like a firefly (2s) instead of the full-strip
   sweep. Localizes "who finished."
3. **Milestone odometer** — every Nth completion of the day (10/25/50) the
   sweep earns one extra golden pass. Tiny variable reward, still a burst.
4. **Rainstick idle** — overnight ambient: a single dim pixel "drips" down
   the strip every ~30s. Signals "alive and watching" without glow.
5. **Sunrise reset** — quota-window reset celebration as a warm dawn
   gradient sweeping once. Rides the existing reset-dedup store.
6. **Ask heartbeat sync** — two simultaneous asks phase-lock into one
   unified beat instead of two competing blinks.
7. **Turn-length ember** — an agent's block deepens in saturation as its
   current turn ages (fresh = airy, 10min+ = deep). At-a-glance "that one
   has been chewing a while." Pairs with the honest working-timer fill.
8. **Recovered grace note** — when a previously failed session next
   completes cleanly, its celebration opens with one green-over-red wipe.
9. **Notch meniscus** (Screen Bar) — completion plays a single liquid
   surface-tension ripple from center.
10. **Binary heartbeat** (Dot) — with 3+ agents running, the Dot's two
    LEDs swing a single pixel like a slow metronome: "the fleet is big."

Best first candidates: #2 (firefly) and #1 (baton) slot into the existing
completion-sweep path; #5 rides shipped reset plumbing.

## Making it the best agent-awareness surface (2026-08-18 initiative notes)

The shipped fix in this area: while following Alcove, SidePulse no longer
paints its own notch-deep housing under the capsule — Alcove owns the
shell, we own one slim band hugging its lower edge. The clunky black slab
is gone.

Where to take each surface next, in priority order:

**Screen Bar → a true announcer (carries WORDS, per the three-surface law)**
1. Hover reveal: pointing at the band expands a small pill naming the
   session and, when asking, the actual question — click answers or jumps
   (T3Notch's approvals-in-place, scoped to our bar).
2. Per-agent segmentation: hairline gaps between agent blocks so the band
   reads "three agents" at a glance instead of one gradient smear.
3. Completion meniscus: a single liquid ripple from center on completion,
   replacing nothing — it decorates the existing finite cue.
4. Liquid easing: velocity-matched color transitions when state changes,
   so the band never snaps.

**SidePulse Pro (8 LEDs) — satisfying physical light**
5. Firefly completion + handoff baton (see the animation list above).
6. Turn-length ember: hue deepens as a turn ages.
7. Sub-perceptual "alive" drip for overnight runs (rainstick).

**Dot (2 LEDs) — a glanceable semaphore**
8. Binary heartbeat when the fleet is 3+; LED one = most urgent state,
   LED two = fleet size band (off/1/many via brightness steps).

**The ledger (menu + browser) — fastest catch-up**
9. Turn duration + last activity verb per row ("3m · editing tests"),
   from the canonical watermark ages.
10. "Since you left" grouping by provider with one-line outcomes, so
    reopening after an hour reads like a changelog, not a list.

**Engine**
11. The JSC per-frame batching (recorded above) — smoother bar at a
    fraction of the CPU.

Fit check: 1-4 need only the virtual_device draw path + presentation
policy; 5-8 ride the signal/motion vocabulary; 9-10 are menu projection
work. Nothing above touches the interrupt budget.

## Execution

The full brainstorm and its sequencing live in
`docs/superpowers/plans/2026-08-18-make-it-the-best.md` — seven waves,
gated the same way every shipped wave has been.
