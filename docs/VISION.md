# JR-BAR — the vision

Synthesized 2026-08-14 from source-first study of CodexBar (`c4ed34d0`) and
t3code (`b73232bd`) plus research into the notch-utility landscape, the
notification-LED tradition, calm-technology principles, and the current
agent-management market. Opinionated on purpose.

## 1. Thesis

JR-BAR is the ambient nervous system for developers who run more than one AI coding agent at a time. It is not an orchestrator — it never drives your agents, never touches your repo, never asks for GitHub write access. It watches every harness you already use (Claude Code, Codex, Cursor, Gemini, Devin, Grok, OpenCode, Hermes, Antigravity) and answers one question continuously, without you asking: **which one needs you right now?** The sentence that sells it: *"Stop keeping five agents in your head. The light tells you when one wants you, and only then."* The documented pain is not missing features — it is cognitive overload ("you need to keep the state of every agent in your head... the constant context-switching is brutal"; "my brain starts falling apart within minutes"). We sell the end of that, not a dashboard.

THE THREE SURFACES ARE NOT THREE COPIES. Today they are — `virtual_device.py` mirrors the LED animation onto the notch, which means we ship one signal three times and call it a product. That must end. Each surface answers a different question, on a different timescale, in a different part of the visual field:

**LED hardware (SidePulse Pro / Dot) — THE PERIPHERAL.** Question: *does anything want me, and which one?* It lives outside the screen, works when the laptop is on a stand, when you are on a call, when you are across the room. It carries **state, latched, wordless** — persistent until acknowledged, in the BlackBerry sense that made that light beloved: a queue, not a flash. It is read by rods, not cones, so it speaks in brightness and rhythm first, hue second. It carries at most: how many things want you, which slot they are in, and how badly. It carries **no text, no content, no progress bars, no percentages.** Its resting state is **off**.

**Screen Bar (notch) — THE ANNOUNCER AND THE ROLL CALL.** Question: *what just happened, and who is who?* This is the surface that answers the strongest objection to the whole product ("a light tells you something changed; it can't tell you what"). Default state is a near-invisible hairline of identity ticks — one tick per live session, stable left-to-right creation order, so the LED slot map is legible on screen. On a state change it **announces**: expands for ~4 seconds with the harness glyph, the session name, and the actual question ("Claude/api-refactor · Run tests? y/n"). On hover it stays; on hold it expands to the full ask with Allow/Deny inline. Then it collapses back to a hairline. It is **transient and situational**, following Apple's own Live Activity discipline — never a persistent dashboard, never fake-interactive chrome in the compact state. It is also the **teacher**: it is where a user learns that amber-breathing means "needs you," because they see the light and the words at the same moment.

**Menu bar title + dropdown — THE LEDGER.** Question: *what exactly, across everything, and what do I do about it?* Text, numbers, provenance, actions, history, quota. The title is a user-composed token layout capped at two lines (adopt CodexBar's `MenuBarLayout` model wholesale). The dropdown opens with the Needs-You inbox and nothing else above it. This is the only surface allowed to be dense, and it is the only one you have to deliberately go to.

Rule that keeps them honest: **no fact appears on more than one rung of the ladder.** If the dropdown says it, the light does not try to.

## 2. The light language

This is the soul of the product and it is currently wrong in the code. `signals.py` ships nine patterns (breathe, blink, double-blink, solid, sweep, ripple, comet, sparkle, heartbeat), twelve signal types, three escalation tiers, and a user-authored DSL Studio on top. Miller's absolute-judgment ceiling is ~7 categories on a single perceptual channel, and the applied color-coding literature (Christ 1975) puts the practical cap at **4 for casual users**. The Ambient Orb had full RGB and used three states. The build-light tradition that is JR-BAR's true ancestor used two. We are encoding nine patterns × twelve sources and expecting recall that no human has.

**THE STATE AXIS — FOUR LIT STATES PLUS OFF. That is the whole vocabulary.**

| State | Meaning | Color (Okabe-Ito, colorblind-safe) | Rhythm | Peak brightness | Latch |
|---|---|---|---|---|---|
| **NEEDS YOU** | agent is blocked on a human: approval, input, a question | orange `#E69F00` | breathe, 2.0s period, Gaussian ramp | ramps 40% → 100% over 4 min, then **stops** | latched until acknowledged |
| **WORKING** | running, nothing wanted | blue `#0072B2` | breathe, 5.0s period (≈12/min — resting respiratory rate, the number Apple actually shipped in the MacBook sleep light after their own patent specified 33) | 20% ceiling | no |
| **BROKEN** | error, crash, dead process, hung | vermillion `#D55E00` | two pulses at 1.5Hz, then **still** | 80% fixed | latched |
| **DONE (unseen)** | finished since you last looked | bluish-green `#009E73` | one directional sweep (~700ms), then still | 30% | latched until seen |
| **SETTLED / IDLE** | nothing live, nothing pending | **off** | — | 0% | — |

Off is the most important state in the language. Calm technology means the resting condition is darkness, not a running status ticker. If a user's normal working hours look like a dark bar, we have succeeded.

**THE RHYTHM AXIS — FOUR RHYTHMS, EACH MEANING ONE THING.** *Still* = latched and acknowledged, or steady-state error. *Slow breathe (5s)* = alive, unattended, nothing needed. *Urgent breathe (2s)* = wants you. *Pulse/sweep (one-shot)* = a transition just occurred. Rhythm, not hue, is the primary urgency carrier, because the Screen Bar and the LED both sit in peripheral vision where rods dominate — they encode luminance change over time and are largely colorblind. A user reading hue from the corner of their eye is a design assumption the visual system does not support. Kim et al. (HCI 2014) found blink frequency alone, independent of color, drove urgency perception; we lean on that and treat color as the confirmation you get once you have already looked.

**THE SAFETY ENVELOPE — HARD LIMITS, ENFORCED IN CODE, NOT IN STYLE GUIDES.**
- **Nothing above 2.0Hz. Ever.** WCAG 2.3.1 forbids more than three flashes per second. `SIGNAL_NOTIFICATION` currently blinks at 0.3s ≈ 3.3Hz. That is over the line, in a product with a physical light closer to the eye than a screen. Fix it.
- **Saturated red never flashes above 0.5Hz**, because the red-flash threshold is stricter and the photosensitive risk band (3–55Hz) is elevated for red specifically. Note the firmware's own parse-error behavior — six red flashes at 150ms on/off = 3.3Hz saturated red. We cannot change the firmware, so we must make it **structurally impossible for us to emit a program that fails to parse**: every program goes through the real WASM parser before it reaches the device, and a rejected program falls back to "off," never to the device.
- **A clamp function sits between the renderer and every surface.** No caller — not a plugin, not Studio, not a first-party signal — can emit a program that exceeds the envelope. Presentation is chosen by the engine from semantics; senders never pick a frequency.
- **No red/green oppositions.** `#E01010` low-battery against `#34C759` notification is exactly the pairing that fails for ~1 in 12 men. Orange/blue is the most universally distinguishable pairing and it is the pairing we use for our two most important states (needs-you vs working).
- **Every state is legible with color removed.** Rhythm + brightness alone distinguish all four: 2s bright breathe, 5s dim breathe, still-bright, one-shot sweep. Test this by rendering the whole language in grayscale and asking someone to name the states.
- **`accessibilityDisplayShouldReduceMotion` is queried and honored.** Under Reduce Motion the language collapses to a four-step static brightness ladder (0 / dim / mid / bright) with no animation whatsoever, and it is still complete — that is the proof the language is well-formed. Also honor "Differentiate Without Color," which macOS does *not* apply for you: the Screen Bar and menu bar add shape glyphs (dot / ring / cross / check).

**THE POSITION AXIS — IDENTITY IS POSITION, NOT COLOR.** This is the single most important structural decision and it is where Android's per-app LED colors failed: users saw a light and could not remember which app it meant. Color cannot scale past four meanings, and we intend to track many sessions across many harnesses. Therefore: on SidePulse Pro's 8 LEDs, **each live session owns a slot**, assigned in stable creation order and **never reordered by activity** (this is t3code's hardest-won UI lesson: "activity NEVER reorders the list — a row holds its position from open until settled"). The dropdown lists sessions in the same order with the same slot numbers, and the Screen Bar hairline shows the same ticks in the same order. Slot 3 is amber → third row of the dropdown → third tick on the notch. One mental model, three surfaces, zero translation.

Hue-as-identity gets exactly one narrow license: **when nothing is urgent**, a working session may render in a personal identity tint instead of the standard blue. The moment any session enters NEEDS YOU or BROKEN, all identity tinting is suppressed and the bar speaks pure state color. **Urgency preempts identity.** With more than 8 sessions, slots 1–7 are the seven oldest live sessions and slot 8 becomes "and N more," rendered at the highest state present among the overflow.

**DEGRADATION — SidePulse Dot (2 LEDs).** Positional identity is impossible, so Dot does not fake it. LED A = the single highest-priority state present. LED B = "is there more than one" (off = one thing, dim = several). Honest reduction, not a squeezed copy.

**NIGHT PROFILE — built in by default, not left to the user to discover.** The classic BlackBerry-era complaint was that the light kept people awake. After the user's night window (or system sunset), brightness caps at 8%, hues shift warm, one-shot sweeps and pulses are suppressed entirely, and only NEEDS YOU may animate — at the slow rhythm. This also aligns with circadian guidance (blue is the worst offender; warm and dim is the safe zone) and with Apple's own 25%-duty-cycle sleep-light reasoning. And there is a Focus/DND contract, the equivalent of iOS's "Flash on Silent": when the user is in a Do Not Disturb Focus, non-agent signals are fully suppressed and agent NEEDS YOU is capped at 40% with no escalation.

**WHAT WE REFUSE TO ENCODE.** Which harness a session belongs to (position and the Screen Bar carry that; color cannot). Progress percentage or token counts on the light. Subagent depth or count — t3code proved the right move is collapsing a rich tree to one flag for the ambient surface. Message content. Any distinction requiring a legend the user must memorize beyond four states. Music visualization, decorative sweeps, or "fun" animation as a default — Nothing's Glyph reviews are unambiguous that expressive/decorative uses read as gimmick while state-holding uses (battery meter, DND override, countdown) read as genuinely useful. Anything decorative is opt-in polish that ships after the language is loved, never before.

## 3. Information architecture

**THE LADDER. Every piece of information we hold gets assigned to exactly one rung. If it appears on two rungs, one of them is wrong.**

**RUNG 0 — 200ms, no head movement, peripheral vision (LED hardware + Screen Bar hairline).** Budget: about four bits.
- Does anything want me? (any amber present)
- Roughly how many? (count of lit slots — one / a few / the bar is amber)
- Which slot? (position → maps to dropdown row)
- Is anything broken? (vermillion)
- Did something finish that I have not seen? (green, latched)
- Nothing lit = nothing needs me. **This is the answer 90% of the time and it must be reliable enough to trust, because the product's entire value is the glances you *don't* take.**

That is the complete Rung 0 payload. Nothing else goes here. Not quota, not cost, not model names, not progress.

**RUNG 1 — 2 seconds, eyes flick up, no click (menu bar title + icon).** Token layout, max two lines, user-composed from presets (adopt CodexBar's `MenuBarLayoutToken` / `MenuBarLayout` / renderer-with-cache model directly — it is the right abstraction and it is already proven).
- Count of sessions needing you, as a badge: `(3)`
- The single most-constrained quota, as a percent or countdown: `Claude 12% · 1h 5m`
- A state glyph for the worst state present
- Nothing else. **The menu bar is not a dashboard and it is not guaranteed visible** — `NSStatusItem.isVisible` lies when the notch or a long menu title swallows the icon, and macOS silently drops overflow with no user-facing indicator. We must self-check by coordinate overlap and, when swallowed, **route the signal to the Screen Bar and LEDs instead**, which macOS cannot hide.

**RUNG 2 — 5 seconds, one click (dropdown).** Ordered, and the order is fixed:
1. **NEEDS YOU (N)** — pinned at top. One row per blocked session: identity dot + slot number, harness glyph, session name, the actual ask in one line, and inline Allow / Deny / Jump. This is the front door of the product.
2. **Live sessions** — one row each, in creation order, never reordered by activity. Slot number, state dot, harness, elapsed, last action in ≤6 words.
3. **Recently done (unseen)** — collapses on acknowledgment.
4. **Quota lanes** — per provider, per window: percent, reset countdown, and a provenance marker. Adopt CodexBar's `UsageFormatter` conventions verbatim: `<1%` never `0%`; countdowns ceil to the minute and show at most two units ("in 1h 5m", never seconds, never three units); "today / tomorrow / Mon 4:32 PM" for absolute resets; currency pinned to en_US; K/M/B compaction with one decimal below 10 and none at or above.
5. **Freshness** — "Updated just now" / relative under 24h / absolute beyond.
6. Settings, Devices, Quit.

Explicitly **not** in the dropdown: diffs, transcripts, cost history charts, calibration, Studio, LED programs. Those are Rung 3.

**RUNG 3 — a real window, entered deliberately, rarely.** Settings (7 panes, already built — keep the consolidation). Devices and calibration. Session detail with transcript excerpt and full tool-call tree — this is where the rich subagent/workflow structure lives, and it is the *only* place it lives; the ambient surfaces get one collapsed working/monitoring/idle flag. Signal-source manager (permissions, rate limits, mute). History and usage over time. Onboarding / the light-language legend.

**THE DERIVATION RULE.** No session has a stored `status` field, ever. State is a pure function evaluated on every refresh, over orthogonal inputs, with a fixed precedence: `pendingApproval > pendingInput > processAlive&&recentActivity > lastEventError > processDead > settled`. Adopt t3code's tiebreak for the race we will absolutely hit: **a turn with a non-null `completedAt` counts as completed regardless of what its state field says**, because process teardown and the harness's own completion event race each other and a stored field gets tombstoned. This function is pure Python over plain data, has no AppKit dependency, and is unit-tested to death.

**THE FAN-OUT RULE.** One pure `SurfaceDescriptor` is computed per refresh — a plain data structure with zero AppKit in it, describing what every surface should say. Three dumb renderers consume it: an NSMenu reconciler that diffs against the live menu (CodexBar's prefix/suffix `MenuRowShape` diff, to avoid relayout flicker on an open tracked menu), a Screen Bar drawer, and an LED-DSL serializer. This is the structural fix for the god object: **today the descriptor and the renderers are the same 15,523-line file, which is why three surfaces are three copies — because there is no single place that decides what each one is for.**

**ACKNOWLEDGMENT IS PER-SURFACE, NOT GLOBAL.** t3code's read-receipt model applies directly and non-obviously: the LED, the Screen Bar, and the dropdown are three independent viewers of the same truth. Each tracks its own last-acknowledged timestamp. Dismissing the Screen Bar announcement must **not** clear the LED — the light's job is to persist until you actually deal with it. Opening the dropdown clears the dropdown's unseen markers and clears green (done) on the LED, but never clears amber (needs you); only responding to the ask clears amber. That distinction is the entire difference between a notification and a queue, and the queue is what people loved about the BlackBerry light.

**REFRESH.** Adopt CodexBar's `AdaptiveRefreshPolicyCore` shape: a pure decision function over {now, last menu open, last coding activity, low-power, thermal} with menu-recency tiers, a coding-activity clamp, and thermal backoff — plus one JR-BAR addition it lacks: **per-session urgency**, so a session near its context limit or with an unanswered ask polls faster than an idle one. Adopt its request coalescing (one in-flight fetch per source, generation-tracked) as-is. Adopt its **consent gate**: process-list inspection asks first and declining falls back to time-based refresh. We are a passive observer with agent-adjacent access on a developer's machine; consent theater is not optional for us, it is the trust foundation.

## 4. The notification engine

The owner is right that this should be a general notification bar — Apple has left the notch inert and nobody has made the LED mean anything since phones dropped them (and note: **phone LEDs were killed by bezel-less OLED manufacturing, not by user rejection** — that is a fact worth putting in our own marketing, because we are not reviving a dead idea, we are unblocking an interrupted one). But the current implementation is doing this the wrong way and it is already damaging the agent product.

**THE EVIDENCE OF THE PROBLEM.** `docs/ARCHITECTURE.md` states the precedence: `test > escalation takeover > weather > low battery > notification > completion > reminders > calendar > battery > timer > studio > agent`. **Agent is dead last.** In the app whose entire thesis is agent awareness, a severe-weather alert, a reminder, and a calendar glow all outrank the thing the user bought it for. Each new signal type is a new module (`weather_watch.py`, `reminders_watch.py`, `calendar_watch.py`) plus a new row in the arbiter plus a new style constant. That is not an engine, it is a growing switch statement, and it is the exact ad-hoc-per-provider pattern CodexBar is already suffering from in `UsageLimitsAvailability.resolve`. It will not survive twenty sources.

**THE RIGHT ABSTRACTION: THE SIGNAL.** One type, and it is deliberately semantic — senders declare *meaning*, never *presentation*.

```
Signal {
  source_id      # registered, permissioned producer
  subject_key    # stable identity; re-sends update, never duplicate
  class          # AGENT | SYSTEM | AMBIENT      <- the load-bearing field
  state          # needs_you | working | broken | done | clear
  urgency        # 0 notice, 1 attention, 2 blocking  (ceiling set by permission)
  identity       # optional display hint: name, glyph, tint
  title / body   # short text for Screen Bar and dropdown
  actions[]      # optional: label + a callback the user must have approved
  latch          # bool: does this persist until acknowledged
  ttl            # auto-clear deadline
}
```

No colors. No patterns. No frequencies. No brightness. The engine derives all presentation from `(class, state, urgency)` through the one Light Language renderer and the one safety clamp. **This is the anti-mush mechanism**: a plugin cannot make itself louder or prettier than the agent lane because it has no vocabulary for loudness.

**THE STRUCTURAL PRIVILEGE THAT KEEPS AGENTS SPECIAL.** Not priority ordering — priority ordering is what produced the current bug. **Zoning.**
- The LED bar's positional slots are **reserved for `class=AGENT`**. A calendar event, a battery warning, or a build result can never occupy a session slot, no matter how urgent it claims to be.
- SYSTEM and AMBIENT signals get exactly one channel on the light: the **end cap** — the outermost LED (or a 3px cap on the Screen Bar hairline) — and they may only tint it, briefly.
- **Only AGENT signals may latch.** Everything else is transient by construction and self-clears. Your reminder cannot squat on the bar.
- **Only AGENT signals may escalate** over time.
- Non-agent signals get their real expression on the **Screen Bar announcement** (a 4-second card with actual words — which is a *better* notification experience than a colored light anyway) and in the dropdown, not on the hardware.

So the "general notification bar" ambition is fully served — anything can announce, anything appears in the ledger — while the physical light stays a dedicated agent instrument. That is a stronger product than a light that means twelve things, and it resolves the tension without compromise.

**THE EXTENSIBILITY SEAM — two tiers, following CodexBar's proven pattern (69 compiled providers + a sandboxed plugin engine).**

*Tier 1 — first-party adapters.* A lean Python protocol with heavy default implementations, registered in one static catalog, so adding Cursor or Antigravity is one file and the rest of the app does not change. Adopt t3code's **driver-kind vs instance-id split** now, not later: `codex` is the driver, `codex_work` and `codex_personal` are instances, and sessions reference instances. Multi-account is native from day one instead of a migration. Also reserve an `environment_id` field in session identity even though v1 only ever has one value — remote-machine aggregation later becomes additive rather than a schema break.

*Tier 2 — the open Signal API, four doorways onto one socket* **(retired 2026-08-26, owner decision — kept for the record)**:
1. **CLI:** `jrbar signal --source my-ci --subject build:main --state broken --title "3 tests failing" --action "Open log:open ./log"` — this is the doorway that gets us tmux users, cron jobs, Makefiles, and every harness we have not integrated.
2. **Shortcuts action** — non-developers, Focus automations, Stream Deck.
3. **Unix socket + JSON schema** — the same contract the first-party hook path uses, so nothing is second-class.
4. **A declarative manifest** (`signal.toml`: source name, glyph, tint, default class/urgency ceiling, poll command) so a source can be *pulled* as well as pushed, and so someone can add a harness without writing code.

Crucially: `jrbar signal` also **reads** — `jrbar status --json` gives the whole snapshot. Adopt CodexBar's stated non-goal discipline here: we expose a stable local JSON/CLI surface and let the community own Linux widgets, Raycast extensions, and Stream Deck plugins rather than absorbing them. We own three surfaces well; we do not own every surface.

**THE TRUST AND PERMISSION MODEL.** This matters more for us than for anyone in the category, because our user is a developer running agents with shell access who is *already* nervous, and because Conductor took a public beating for requesting broad GitHub scopes to do far less than we do.
- **Sources register and the user approves once**, via a card in the dropdown: *"`my-ci` wants to show signals. Allow / Allow (attention level) / Deny."* Approval is per-source and per-session-of-approval, never inferred.
- **Every source has an urgency ceiling.** Default ceiling is `notice` — it can appear in the ledger and briefly announce, but it cannot make the light demand anything. Promoting a source to `attention` is an explicit user act. **No source can ever reach `blocking`; only first-party agent adapters can, and only for a real human-blocking ask.**
- **Presentation is never sender-controlled.** No RGB, no frequency, no brightness in the API. This is a safety property (WCAG flash limits are enforced centrally) and a design property (the language stays learnable).
- **Rate limiting per source**, token bucket. A source that floods gets auto-muted with a visible note in the dropdown and a one-click un-mute. A misbehaving script must not be able to make the hardware unusable.
- **Actions are opt-in and explicit.** A signal may propose an action; running it requires the user to have approved that source for actions, and the command is shown in full before first execution. Adopt CodexBar's decoupled hooks concept — the same crossing-event stream feeds OS notifications, shell hooks, and the LED writer — but never let an inbound signal define an outbound command silently.
- **Public non-goals, stated in the README on day one:** no background Screen Recording, no background Accessibility permission, no filesystem crawling (we read a small set of known harness paths, and only for enabled harnesses), process-list inspection is consent-gated, no credentials stored, no network egress except explicit provider quota checks. And adopt CodexBar's identity-siloing rule verbatim: **never render one harness's account/plan/identity data in another harness's row.** Cross-harness aggregation is precisely where that bug class appears.

## 5. What to build, ranked

### 1. Light Language v1: one vocabulary, one renderer, one enforced safety clamp  ·  _medium_

**What.** Collapse signals.py from 9 patterns / 12 signal types to 4 states + off and 4 rhythms, with the Okabe-Ito palette (orange/blue/vermillion/bluish-green), position-as-identity, brightness-as-urgency, and a clamp function every emitted program must pass. Delete ripple, comet, sparkle, heartbeat, and the freeform pattern picker. Fix #34C759 blink@0.3s (3.3Hz) and the #E01010/#34C759 red-green pairing. Route every program through the real WASM parser before the device so we can never trigger the firmware's 3.3Hz saturated-red parse-error strobe. Write the language down as a one-page spec that ships in the repo and in onboarding.

**Why.** This is the product. Everything else is plumbing around it. Nine patterns is unlearnable (Christ's cap is ~4 for casual users; the build-light tradition and the Ambient Orb both used 2-3), and we are currently shipping above the WCAG 2.3.1 flash threshold in saturated red on a physical light — a real liability once hardware ships to strangers.

**Proof it worked.** A developer who has never seen the app names all four states correctly after a 60-second onboarding, and again 48 hours later with no reminder. Automated test asserts no emitted LED program exceeds 2.0Hz, that saturated red never exceeds 0.5Hz, that every program parses, and that all four states remain distinguishable when rendered in grayscale.

### 2. Signal core + zone arbiter: kill the 12-row precedence table  ·  _large_

**What.** One pure Signal type (source/subject/class/state/urgency/identity/text/actions/latch/ttl) and one pure arbiter that allocates the LED bar by ZONE — positional slots reserved for class=AGENT, a single end cap for SYSTEM/AMBIENT, latching and escalation available only to agents. Replaces `test > escalation > weather > low battery > notification > completion > reminders > calendar > battery > timer > studio > agent`. Session state becomes a pure derivation function over orthogonal inputs (pendingApproval / pendingInput / processAlive / lastEventState / completedAt), never a stored field, with the completedAt-wins tiebreak.

**Why.** The current arbiter ranks agents LAST in an agent product — that is not a bug, it is the predictable output of a design where every new source is a new priority row. Zoning fixes it structurally and makes the general-notification-bar ambition safe to pursue. Pure derivation eliminates the whole class of stale-status bugs we will otherwise chase forever across three surfaces.

**Proof it worked.** Weather, calendar, and battery signals cannot occupy an agent slot under any input; a fuzz test over random signal sets shows the agent lane never preempted. Session status is computed by one function with 100% branch coverage and zero AppKit imports.

### 3. Decompose status_bar.py: a pure SurfaceDescriptor plus three dumb renderers  ·  _epic_

**What.** Extract a plain-data SurfaceDescriptor (what every surface should say this tick — sections, entries, actions as pure values, no AppKit, no closures) from the 15,523-line status_bar.py. Then three renderers consume it: an NSMenu reconciler that prefix/suffix-diffs against the live menu to avoid relayout flicker, a Screen Bar drawer, and the LED-DSL serializer. Move the god object's timers and watchers into a scheduler module.

**Why.** You cannot fan one signal out to three DIFFERENT surfaces from a file where the decision and the drawing are the same code — which is exactly why the three surfaces are currently three copies. CodexBar's MenuDescriptor has ~20 dedicated test files precisely because it has no AppKit dependency; we have almost no equivalent testability. This unblocks builds 1, 2, 4, and 5 and is the highest-leverage refactor in the codebase.

**Proof it worked.** status_bar.py under 3,000 lines. A full dropdown, Screen Bar state, and LED program can be asserted in a unit test with no NSApplication, no status item, and no device. Opening the menu during a refresh produces zero visible row flicker.

### 4. Screen Bar rebuild: announcer and roll call, not an LED mirror  ·  _large_

**What.** Stop mirroring LED animations onto the notch. New behavior: a near-invisible hairline of per-session identity ticks in stable creation order at rest; a ~4-second expanded announcement card on state change carrying the harness glyph, session name, and the actual question in words; hover to hold; hold to expand with inline Allow/Deny. Borderless, click-through where non-interactive, non-activating NSPanel at .statusBar level joining all Spaces. Geometry from NSScreen.safeAreaInsets / auxiliaryTopLeftArea, never a per-model pixel table; safeAreaInsets.top == 0 triggers a floating-pill fallback at the same logical position.

**Why.** This answers the strongest objection to the entire product — 'a light tells you something changed, it can't tell you what.' If the answer to every light is 'go open the dropdown,' the light saved a glance and cost a context switch, and the hardware tier is jewelry. The Screen Bar carrying the WHAT is what makes the light sufficient. It is also the surface that teaches the language. And nobody in the notch-app category models multi-agent state at all — this is the unclaimed territory.

**Proof it worked.** A user can respond to an agent's approval request without ever opening the dropdown or switching to the terminal. Non-notched Macs and external displays render an identical-feeling pill. The Screen Bar never obscures fullscreen content (NotchNook's documented failure) and has a fast, real off toggle (a real segment of users actively wants the notch neutralized).

### 5. Needs-You inbox as the product's front door, with per-surface acknowledgment  ·  _medium_

**What.** Promote the existing Ask Inbox to be the top and default content of the dropdown: one row per blocked session with slot number, identity dot, harness glyph, the ask text, and inline Allow/Deny/Jump. Menu-bar badge (N). Then implement per-surface read receipts: LED, Screen Bar, and dropdown each track their own last-acknowledged timestamp. Dismissing the Screen Bar does NOT clear the LED; opening the dropdown clears green (done) but not amber (needs you); only answering clears amber.

**Why.** 'Which one needs me' is the entire job. And the latch semantics are what made the BlackBerry light beloved — it was a queue you could trust while you were away from the desk for two hours, not a flash you might miss. A single global read flag would let a glanced-at notch silently clear the hardware, which is the one behavior that would destroy trust in the light.

**Proof it worked.** Amber persists across a Screen Bar dismissal, a dropdown open, a display sleep, and an app restart, and clears only on an actual response. Time-to-respond to an approval request drops measurably versus terminal-polling in a self-timed trial.

### 6. Harness coverage tier 2: transcript-first adapters plus a declarative manifest  ·  _large_

**What.** Stop depending on hooks as the primary ingestion path. Read each harness's own on-disk session transcripts directly (~/.claude/projects/**/*.jsonl, ~/.codex/sessions/**/*.jsonl, and equivalents) as the default source, with hooks as a low-latency accelerant where available. Add a declarative harness manifest (paths, JSONL event shapes, state mapping, quota endpoint) so a new harness is config, not code. Adopt the driver-kind vs instance-id split and reserve environment_id. Deduplicate sources by filesystem device:inode, not hostname+path.

**Why.** 'Every harness or provider' is the promise, and a hooks-first pipeline can only see harnesses that support hooks AND that the user successfully configured. t3code reads transcripts specifically so usage stays complete for turns never driven through their product — we are a passive observer, so this applies to us even more strongly. Until this lands, our coverage claims are aspirational. ClaudeBar already lists 12 providers; CodexBar lists 69. We cannot win on breadth with bespoke integrations.

**Proof it worked.** A harness we have never integrated becomes visible by adding one manifest file with no Python changes. Sessions started before JR-BAR was installed appear correctly. Two JR-BAR instances reading the same physical directory do not double-count.

### 7. The open Signal API and its trust model  ·  RETIRED

**Retired 2026-08-26 (owner decision).** The open Signal API — the
`jrbar signal` CLI, Shortcuts action, socket schema, pull-mode manifest,
and its permission layer — is off the roadmap. The full entry lives in
git history; nothing here should be built or claimed. (`sidepulse serve`
remains the one shipped read-only loopback surface.)

### 8. Quota lane with honest provenance — and cut the bespoke forecasting  ·  _medium_

**What.** Keep quota as a first-class lane (a generic three-window + named-extras + label/value-escape-hatch schema, per CodexBar's UsageSnapshot/RateWindow shape) with threshold-crossing-with-hysteresis notification: fire only on an actual crossing, mark all higher thresholds fired so one 30%->2% drop is one alert not three, re-arm on recovery. Tag every number with its provenance (provider-reported / model-priced / unpriced) and distinguish 'this harness has no quota API' from 'we have not heard from it yet' via a declarative capability flag on the adapter, not a switch-by-name. Adopt UsageFormatter's conventions wholesale. CUT the bespoke capacity_forecast / capacity_calibration / capacity_history machinery. (Done 2026-08-26 for capacity_forecast and capacity_calibration; capacity_history stays, behind retention consent.)

**Why.** Quota anxiety is independently documented and real ('I limited out very fast this morning without even writing code'), and it is a genuinely different failure from 'my agent finished' — you can know an agent finished and still get blindsided by a dead quota mid-task. But we currently have five capacity modules doing prediction work that CodexBar does better with a fraction of the code, and prediction against an undocumented, changing token contract is a treadmill we will lose. Report what we know, honestly, and stop guessing.

**Proof it worked.** A user hits 90%, 75%, 50% remaining and receives exactly three alerts, in order, with no burst on a fast drop and correct re-arming after reset. No number appears in the UI whose provenance we cannot name. Net line count in the capacity modules goes DOWN.

### 9. Accessibility, night, and distribution: the trust package  ·  _medium_

**What.** Query and honor accessibilityDisplayShouldReduceMotion (all rhythms collapse to a four-step static brightness ladder that remains fully expressive) and Differentiate Without Color (shape glyphs on screen surfaces). Ship the night profile on by default: after the user's night window, 8% brightness cap, warm hue shift, no one-shot animations, attention only at the slow rhythm. Honor Focus/DND as a first-class suppressor with an agent carve-out. Detect our own menu-bar icon being swallowed by the notch or overflow via coordinate overlap and reroute to the Screen Bar/LEDs. Then sign and notarize, publish a real privacy policy, and state the non-goals in the README.

**Why.** None of the OS accessibility settings apply automatically — flipping the system switch does nothing unless we check for it. Roughly 1 in 12 men cannot read a red/green scheme. The category has documented, specific failures here: Boring Notch ships unsigned with Gatekeeper friction and battery complaints from an always-on visualizer, NotchNook shipped without a privacy policy while requesting calendar access. Our user is a developer deciding whether to trust a near-system-UI process on a machine running agents. Getting this wrong is disqualifying, not deductible.

**Proof it worked.** The full language is nameable under Reduce Motion with all animation disabled and under grayscale. The app installs on a clean Mac with no Gatekeeper override. Overnight idle battery draw is under 1%/hr with agents running. NSStatusItem occlusion is detected and rerouted in a real notch test on both a 14-inch and a 16-inch machine.

### 10. Hardware-optional parity plus the 30-second legend  ·  _medium_

**What.** Make the zero-hardware experience complete: the Screen Bar and menu bar must be excellent alone, with a virtual 8-slot bar in the dropdown that shows exactly what SidePulse Pro would show. Honest Dot degradation (LED A = highest state, LED B = one-vs-many; no faked positional identity). No-notch and external-display fallback to a floating pill. And a 30-second onboarding that TEACHES the light language by lighting each state while naming it, ending with 'this is what your bar looks like when nothing needs you: off.'

**Why.** This is the growth loop and the honesty test in one. If the software is only good with hardware, the hardware never sells, because nobody buys a $50 accessory for an app they have not fallen in love with. ai-pulse already exists as a purely virtual SidePulse-inspired LED strip and cites us as inspiration — that is proof the virtual version is a viable product on its own, and a warning that someone else will ship it well if we do not. The legend matters because a language nobody is taught is just pretty lights.

**Proof it worked.** A user with no hardware describes the product accurately and would recommend it. Post-onboarding, a new user correctly names all four states. Attach rate from software users to hardware purchase is measurable and non-trivial.

## 6. The grilling

**You have built a signal engine and called it a product.** Fifteen thousand five hundred lines in one file, nine LED patterns, twelve signal types, three escalation tiers, a user-authored DSL Studio, Day/Night/Travel calibration profiles, a timebox with a chime, per-device blend overrides, weather integration with IP geolocation, and a Focus-to-profile automation engine — and the precedence table ranks **agent dead last**, behind weather. That is not an oversight. It is the honest output of a system where the interesting engineering problem (light) has been allowed to outrank the actual customer problem (which agent needs me). MediaMate is the most uniformly praised app in this entire category and it does exactly two things. NotchNook is the most feature-rich and has the most mixed reviews. You are building NotchNook.

**The hardware is probably not the differentiator, and betting the roadmap on it is the biggest strategic risk.** The vision leads with the LEDs, and LEDs are the emotionally exciting part — the 2000s-phone nostalgia is genuine and the "nobody has done this right" instinct is correct. But: ai-pulse already ships a purely virtual SidePulse-inspired LED strip and explicitly cites your hardware as its inspiration. AgentDeck already drives 26 hardware surfaces. vibesignal already ships a physical USB light for Claude Code and Codex, with an on-screen fallback so you can try before buying — which is exactly the go-to-market you have not built. You are not first to physical agent status lights. You are only better-positioned. And you are attaching hardware economics (BOM, firmware, RMA, returns, inventory, a category where the SD-slot device gets power-cut after 3 minutes and needs a keepalive touch loop) to a software category where the price ceiling has been established at $15–20 one-time. **The differentiator is harness coverage plus a learnable language. The hardware is the moat you build after product-market fit, not the wedge you lead with.**

**The strongest objection to the whole product has not been answered, and you should sit with it.** From 5dive: *a light tells you something changed; it can't tell you what.* If every amber LED resolves to "go open the dropdown," you have converted one context switch into one context switch plus a glance. Nothing was saved. Right now the Screen Bar mirrors the LED animation, which means it also cannot tell you what. **Until the Screen Bar carries the actual question in words, the LED tier is decoration.** This is why build #4 is ranked where it is: it is not polish, it is the load-bearing answer to "why does this beat a terminal bell."

**Coverage is the promise and hooks are the mechanism, and they don't match.** "Every harness or provider" is the whole positioning, but hook-based ingestion only sees harnesses that ship hooks AND that the user actually configured. t3code reads the harness's own on-disk transcripts specifically so their numbers stay complete for turns never driven through their product — you are a *passive observer*, so that argument is stronger for you than for them, and you are on the wrong side of it. ClaudeBar already covers 12 providers. CodexBar covers 69 with a plugin escape hatch for the tail. Any coverage claim you make today is aspirational.

**What CodexBar does better, plainly:** a pure, AppKit-free MenuDescriptor with ~20 dedicated test files versus your untestable god object; one centralized UsageFormatter with genuinely good human-first conventions versus formatting scattered wherever it was convenient; an adaptive refresh policy shipped as a separate target with an offline trace-replay harness to validate policy changes *before* shipping them; a two-tier provider model (lean protocol with heavy defaults + sandboxed QuickJS plugins) that makes 69 providers tractable; and explicit, published non-goals with a merge-by-default vs needs-sign-off split that resists exactly the scope creep you are experiencing. **What t3code does better:** status as pure derivation with a documented race tiebreak; the absolute refusal to reorder a list on activity; per-device read receipts; usage read from the tool's own transcripts with three-way cost provenance; and the driver-kind/instance-id split that makes multi-account native instead of a future migration.

**You are shipping something above the flash-safety line.** `SIGNAL_NOTIFICATION` blinks at 0.3s — about 3.3Hz, over WCAG 2.3.1's three-per-second limit. The firmware's parse-error path strobes saturated red six times at 150ms, which is 3.3Hz in the single most seizure-elevated color. Your own architecture doc records that emitting `N:off` triggers that strobe and that it *regressed twice*. Your low-battery red against your notification green is the exact pairing that fails for roughly 1 in 12 men. These are not nitpicks on a screen app; this is a light physically closer to a user's eye than a display, shipping to strangers, with the maker's name on it.

**Things you have built that nobody wants.** Studio (user-authored LED DSL programs) — the users who want this number in the dozens and they can have the CLI. Weather on the agent bar — this is the purest scope-creep tell in the codebase and it currently outranks agents. Day/Night/Travel calibration profiles — three profiles is two too many; you need one night rule that is on by default. The timebox with a chime — that is a Pomodoro app, and it is not why anyone installs this. Per-device blend overrides. Five capacity modules doing forecasting against an undocumented token contract that the provider changes without telling you. Every one of these was a satisfying day of work and none of them move the thesis.

**Where you are likely wrong, personally.** You are optimizing for the demo — the moment someone sees the notch bracket ripple in sync with eight physical LEDs and says "whoa." Alcove wins that moment and reviewers still say NotchNook is the one that *stayed installed*. Beautiful animation earns the first install and never the second. The thing that earns the second install is that at 4pm on a Thursday with five agents running, the bar is dark, and you believe it.

**What kills this product.** (1) It becomes an LED toy: gorgeous, three harnesses supported, and the honest use case is "watch the pretty lights." (2) It becomes a second dashboard: so much information that people alt-tab to it, which violates the peripheral principle outright and makes it another thing to check. (3) A trust failure: unsigned build, a battery-drain thread, or one Accessibility permission request too many, and a developer audience that is already jumpy about agent tooling decides not to run it. (4) The refactor never happens, status_bar.py hits 25,000 lines, the three surfaces stay three copies, and every new feature makes the next one more expensive — which is the failure mode you are currently on the trajectory for.

**The uncomfortable truth about the freeze.** The roadmap says "QUALITY MODE — feature freeze — enhance, clean, fix," and then lists a fourth wave of shipped features and a fifth wave of approved ones. The freeze is not holding. It is not holding because building features is more fun than deleting them, and the deletions in build #1, #2, and #8 are the highest-value work available right now.

## 7. Open decisions

### Does the Screen Bar mirror the LED animation, or carry different information entirely?

_Options._ (a) Keep the mirror: the notch shows what the hardware shows, one visual language, cheap to maintain, and the notch becomes a demo of the hardware. (b) Differentiate: the notch becomes the announcer/roll-call that carries WORDS — the session name and the actual question — while the LED carries wordless latched state.

**Recommendation.** (b), decisively. The mirror is the reason the three surfaces are currently three copies, and it leaves the strongest objection to the product unanswered — a light cannot tell you WHAT. If the notch carries the what, the light becomes sufficient and the hardware becomes valuable. If it does not, the hardware is jewelry. This is the single most consequential call in the document.

### Is the general-purpose notification bar a v1 feature or a v1.1 capability?

_Options._ (a) Ship it now: calendar, weather, reminders, battery, builds all in v1, as the app already partly does. (b) Build the ENGINE now (the Signal type, the zone arbiter, the class-based privilege) but ship only the agent lane in v1, open the API in v1.1, and add first-party non-agent sources only after the agent lane is loved. (c) Defer the abstraction entirely.

**Recommendation.** (b). The abstraction must be built now — retrofitting it later is the expensive version, and doing it correctly is what stops the general-purpose ambition from eating the agent product (it already has: agent is last in the current precedence table). But shipping twelve signal sources in v1 is how we become a notch-widget platform nobody remembers. Cut weather immediately; it is the clearest scope-creep marker in the codebase. Calendar/reminders/battery can return as plugins through the public API, on the end cap, unable to latch.

### Hardware-first or software-first go to market?

_Options._ (a) Lead with SidePulse Pro/Dot; the app is the driver for the hardware. (b) Lead with free or cheap software (Screen Bar + menu bar, fully excellent alone); hardware is an upsell to people already in love. (c) Bundle.

**Recommendation.** (b). Nobody buys a $50 accessory for an app they have not already come to depend on, and the category price anchor for the software is $15-20 one-time. vibesignal already ships an on-screen fallback specifically so people can try before buying hardware, and ai-pulse is a purely virtual version of your own device that exists because the software case stands alone. Build the virtual 8-slot bar into the dropdown so every software user sees exactly what the hardware would show — that is the conversion mechanism. Suggested packaging: free tier (Screen Bar + menu bar + 3 harnesses), $25 lifetime Pro (unlimited harnesses + quota lanes), hardware sold separately and never required.

### Divergent-but-merge-friendly fork, or hard divergence?

_Options._ (a) Keep upstream merges viable — constrains how much of status_bar.py can be restructured. (b) Diverge hard: gut the god object, restructure around the SurfaceDescriptor, accept that upstream merges become manual cherry-picks.

**Recommendation.** (b), and this explicitly revises the direction locked on 2026-08-11. You cannot both extract a pure descriptor layer out of a 15,523-line file and stay merge-friendly with the file it came from — the choice is already effectively made, it just has not been admitted. Make it deliberate: take a final upstream merge now, tag it, then diverge. Mitigate by keeping the LED DSL serializer and device-discovery layer close to upstream shape (that is where firmware-driven upstream changes will actually land) and diverging freely everywhere above it.

### Identity encoding: hue per session, or position per session?

_Options._ (a) Keep the 8-hue identity palette as the primary way you tell sessions apart. (b) Position is identity (slot N = row N in the dropdown = tick N on the notch); hue is reserved for the four states, with an identity tint permitted only while nothing is urgent.

**Recommendation.** (b). This is the lesson Android's per-app LED colors taught at scale — users saw a light and could not recall which app it meant, and that was with a handful of apps. Hue tops out around four reliably distinguishable meanings for a casual user, and we are spending all four on state. Position scales to eight, is learnable in one look because it maps 1:1 onto a list the user already reads, and it degrades honestly on the 2-LED Dot (which simply does not claim positional identity). Urgency preempts identity: the moment anything needs you, the bar speaks pure state color.

### Stay PyObjC, or begin a Swift migration?

_Options._ (a) Stay PyObjC, as locked. (b) Rewrite in Swift for AppKit fidelity, performance, signing simplicity, and closer kinship with CodexBar. (c) Stay PyObjC for the shell, but extract the pure core into a language-agnostic, heavily tested package behind a JSON contract.

**Recommendation.** (c). Do not rewrite now — a rewrite would consume the entire runway and produce the same product. But the work in builds #1-#3 (light language, signal core, surface descriptor) is pure logic with no AppKit in it, and if it lands as a standalone, fully tested Python package speaking a documented JSON contract, then a later Swift shell is a mechanical port of the UI layer rather than a from-scratch rebuild. It also gives us `jrbar` as a real CLI for free, the community-extensibility seam. This preserves the locked PyObjC direction while removing its long-term cost.
