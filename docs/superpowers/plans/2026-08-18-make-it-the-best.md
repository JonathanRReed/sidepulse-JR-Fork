# Make it the best — master execution plan (2026-08-18)

Everything from `docs/ECOSYSTEM-RESEARCH.md` plus the wider brainstorm,
sequenced into waves. Standing rules apply to every wave: reachable or it
isn't done; test the seams; nothing above 2Hz; no fact on two rungs; the
interrupt budget is law. Each wave lands green (full suite + ruff +
ratchets) and restarts the LaunchAgent before the next begins.

## The brainstorm, consolidated

**Intelligence depth** (the ledger stops reporting events, starts
reporting meaning)
- Outcome summaries: a completion row carries WHAT happened — files
  touched, tests run, exit state — mined from the transcript pipeline.
- Attention triage scoring: asks ranked by blast radius (prod-deploy
  approval ≠ read permission); LED urgency follows stakes, not recency.
- Turn forecasting: per-provider/project historical turn lengths ->
  "usually ~4m, now 12m" — answers "is it stuck?" honestly.
- Cost ticker: live $/session and per-day burn on the usage row, from the
  priced records that already exist.

**Two-way interaction** (the announcer carries words BOTH directions)
- Answer-from-menu: reply to an ask in a text field inside the dropdown.
- Hover-reveal pill on the Screen Bar: session name + the actual
  question, answer or jump in place.
- Global hotkey palette: jump-to-asking-session without the mouse.
- Screen Bar as drop target: drag text/file onto the band to send it to
  the focused session.

**Fleet & remote** (the multi-Mac vision, unified)
- One fleet view: machine becomes a column; SFTP ledger rows and T3
  remote threads land in the same shelves. Pro = local, Dot = fleet.
- Phone glance: read-only ledger over Tailscale (the t3code-mobile
  pattern), for couch checks.

**History & reflection**
- Daily agent journal: end-of-day summary (sessions, completions,
  failures, cost, busiest hour) from the operator-history store.
- LED time-lapse: replay the day's light history in ten seconds on the
  strip. Fun, and genuinely answers "what happened overnight".

**Hardware & firmware**
- Firmware intro/loop split (upstream request): "play lines 1..k once,
  then loop k+1..n" — unlocks rich one-shot celebrations the
  repeat-replays-everything limitation currently forbids.
- Device role presets: Pro = fleet detail, Dot = asks-only semaphore.

**Robustness**
- Self-watchdog: doctor reports event-latch health, breaker state, event
  freshness lag per provider (tonight's latch bug would have been visible
  in one line).
- Run the signed release gate (`verify_macos_release.sh`) and ship a
  notarized build so TCC grants survive moves.

**Ecosystem** (the "universal indicator" endgame)
- Publish the canonical hook/ledger schema as a small spec; loopback
  read-only JSON API — anything can render agent state.
- Stream Deck plugin: one key per agent, lit by state, press to jump.
- T3Notch interop: feed our ledger into its cards, or adopt its
  hover-card patterns natively.

## Execution waves

> **Status 2026-08-18:** Wave 1 items 1-2 SHIPPED (batched JSC stepping
> with parity + engine-call-count tests; doctor `event_intake_freshness`
> watchdog, manifest v3). Item 3 (signed release) needs the owner's
> signing ritual. Wave 2's band-to-capsule fit shipped early (the bar now
> hugs Alcove's lower contour; no double shell). Firefly (Wave 3) blocked
> on one design call: how a per-agent celebration scopes its LEDs inside
> the whole-strip signal-style model when identity assignment shifts
> mid-sweep — owner to pick between (a) freeze assignment for the burst,
> or (b) follow the live assignment.

### Wave 1 — Engine honesty and smoothness (foundation)
1. JSC frame batching for the Screen Bar renderer (target: motion CPU <=
   2.5%; Instruments before/after). ATTEMPTED 2026-08-18 and REVERTED
   with a finding: whole-program frame memoization is unsound — the
   engine advances each LED's segments on independent cycles, so the
   global period is an LCM far beyond the parser's loop_duration_ms
   (empirical probe found no period under 6.5s for the standard relay).
   The viable designs are (a) per-LED period modeling, or (b) batching N
   future frames per JSC call (amortizes marshaling without assuming
   periodicity). Start from (b).
2. Doctor self-watchdog: event-latch flag age, breaker sentinel state,
   per-provider freshness lag (log watermark vs app watermark).
3. Signed release gate run + notarized install.
Gate: perf evidence file, doctor lines covered by tests, release
checklist in docs/PRODUCTION-RELEASE.md satisfied.

### Wave 2 — The announcer (Screen Bar becomes useful, not just pretty)
4. Hover-reveal pill: session name; the question when asking; click =
   answer-in-place (approve/deny) or jump.
5. Per-agent hairline segmentation of the band.
6. Liquid easing on state transitions; completion meniscus ripple.
Gate: menu-action sweep extended to bar interactions; Reduce Motion
honored; wasm-parity tests for any new program shapes.

### Wave 3 — Celebration & motion vocabulary
7. Firefly completion, handoff baton, sunrise reset, milestone odometer.
8. Turn-length ember, rainstick idle, ask heartbeat sync, recovered
   grace note, Dot binary heartbeat.
Gate: every new pattern through the presentation safety compiler + real
firmware parser; flash-budget tests; owner picks defaults per pattern.

### Wave 4 — Ledger intelligence
9. Turn duration + last-activity verb per row.
10. "Since you left" as a per-provider changelog with outcomes.
11. Outcome summaries from transcripts; cost ticker.
12. Attention triage scoring; turn forecasting.
Gate: no new always-on collection without the existing consent switches;
menu latency P95 unchanged.

### Wave 5 — Two-way interaction
13. Answer-from-menu text field; global hotkey palette; drop target.
Gate: every input path is provider-capability-gated (only providers with
a resume/answer channel get the affordance).

### Wave 6 — Fleet
14. Machine column unification (SFTP ledger + T3 remote rows in one
    shelf model); device role presets.
15. Phone glance (read-only, Tailscale-only, token-authed like cloud
    ingest).
Gate: the no-capacity-crosses-the-wire and no-remote-command laws hold;
identity from channel, never payload.

### Wave 7 — Ecosystem
16. Loopback read-only JSON API + published schema.
17. Stream Deck plugin; T3Notch interop exploration.
Gate: API is versioned, token-authed, off by default.

### Standing candidates (event-driven, not scheduled)
- Kiro provider: flips on the day Kiro is installed (code is ready).
- SSH remote monitoring: re-evaluate after Wave 6 against T3 coverage.
- Firmware intro/loop: file upstream; adopt when released.

## Suggested order of attack
Wave 1 first (everything else renders through it), then Wave 2 — the
single biggest leap in daily value. Waves 3 and 4 can interleave by
appetite; 5-7 build on stable 2/4.
