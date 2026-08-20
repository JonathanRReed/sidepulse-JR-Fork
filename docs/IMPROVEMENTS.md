# Improvements, from user stories

> **Historical snapshot (2026-08-14).** Open work is tracked in
> [`docs/superpowers/plans/2026-08-18-make-it-the-best.md`](superpowers/plans/2026-08-18-make-it-the-best.md).

Written 2026-08-14, after walking real usage rather than reading code. Each
story is something the owner actually does; each finding is what breaks when
you follow it end to end. Ordered by how much harm it does today.

---

## The stories

1. **"I glance up mid-task."** Five agents running, one asks a question.
2. **"I walk away and come back."** What happened while I was gone?
3. **"I'm about to run out of Opus."** How much is left, when does it reset?
4. **"I have two Macs."** Agents on both, one pair of eyes.
5. **"I just installed it."** Nothing is configured. What do I see?
6. **"Something is wrong."** The lights are doing something I don't expect.
7. **"I'm in a meeting."** Do not blink at me.
8. **"I want it to look like mine."** Colours, animations, placement.

---

## Found and fixed this round

**Colour disagreed between the two surfaces** (story 1, 8). Channel gain
corrects one strip's LED die response and was applied only on the way to
hardware; the Screen Bar kept the nominal colour. On this device the
correction is green x0.38, so white is driven as `#FF61FF`, yellow as
`#FF6100`, cyan as `#0061FF`. Linked (the default), the Screen Bar now
previews what the strip is actually driven with.

**Likely root cause underneath it, not yet changed:** that gain is applied
to *gamma-encoded* sRGB. If 0.38 was meant as a luminance correction, the
sRGB-domain factor should be `0.38 ^ (1/2.2)` = **0.64**, which drives white
as `#FFA4FF` instead of `#FF61FF` — far less hue shift. If instead 0.38 was
dialled in by eye against the current maths, it is already "right" and the
maths should be left alone. **This needs one look at the strip to settle,
which is why it was not changed blind.**

---

## Recommended next, in order

### 1. Colour correctness (story 1, 8)
- Settle the gamma question above; then apply gain in linear light and
  migrate stored values so nobody's calibration silently changes.
- **Luminance-matched palette.** Green at full drive is 2-3x brighter than
  red or blue, so "green = working" reads as louder than "red = blocked" —
  the opposite of the intended priority.
- **Differentiate without colour.** ~8% of men have a colour vision
  deficiency; red/green is the worst possible pairing for blocked/working.
  Every state needs a distinguishable *motion* as well as a hue.

### 2. Make the ledger answer "what did I miss" (story 2)
The dropdown shows what is happening now. After ten minutes away, the
question is what *changed*. A short "since you left" list — completions,
questions, failures, with timestamps — turns a status light into a log.

### 3. Finish the capacity plane (story 3)
`capacity_view.py` is 1,139 lines of tested, unreachable presentation code.
The gap is a declared lane set for consumer Claude (see
`docs/WAVE-STATUS.md`). Until then the app cannot answer the single question
the owner opens CodexBar for.

### 4. First-run honesty (story 5)
Today a fresh install shows "Idle" whether it is idle, unconfigured, or
broken. Distinguish *no agents*, *no hooks installed*, and *hooks installed
but silent* — the last one is precisely the failure that opened this
project, and it looked identical to idle for an hour.

### 5. A "why is it doing that" surface (story 6)
One panel: which agent, which state, which rule produced the current light,
and when it last changed. Every debugging session so far has started by
reading a log the user cannot see.

### 6. Focus and meeting awareness (story 7)
Respect Do Not Disturb and Focus modes as a first-class input to the
interrupt budget, not a dimming afterthought. Blocked agents may still
escalate; usage and weather must not.

### 7. Multi-machine (story 4)
Tailscale peer discovery, remote rows muted in the interrupt budget by
default. The second Mac's agents should appear in the ledger without
competing for the lights.

---

## Smaller things worth doing

- **Bound the notch render to what changed.** The Screen Bar repaints on a
  timer even when the frame is identical; dedupe by rendered identity the
  way the LED path now does (that fix removed every idle write to hardware).
- **Per-provider "last heard from"** in the dropdown, so a dead hook is
  visible instead of looking like an idle agent.
- **Make `doctor` the first thing offered when a provider goes quiet**,
  rather than something the user must know to look for.
- **A test that runs the packaged bundle**, not just the source tree. Two
  of the worst bugs this round were only visible in the installed app.
- ~~**Retire the remaining dead modules or wire them.**~~ **Done.** Every
  module in `KNOWN_UNWIRED` was decided, not re-described. `capacity_view`
  and `capacity_history_store` were wired into the "Why Is It Doing That?"
  panel; `provider_runtime`, `delivery_ledger_store` and `reply_classifier`
  were deleted with their tests. The list is empty and the ratchet holds it
  there.
- **Give the ratchet a call-reachability arm.** It measures IMPORTS, so a
  module imported at the top of a live file passes with no caller. That is
  not hypothetical: `delivery_ledger.py` plus the whole delivery-planning
  half of `interruption_policy.py` (~700 lines around `plan_deliveries`)
  read as reachable and are as dormant as anything the pinned list ever
  held. Deciding them means deciding whether the notification path adopts
  them, which touches the locked interrupt budget and is an owner call.
- **Persist the notification dedup across a restart, or prove it does not
  need to be.** `track_completions` dedups from `last_agent_modes`, which
  is in-memory only. If an empty `previous_modes` can yield a completion
  batch, every relaunch re-announces work that already finished — which is
  exactly the job `delivery_ledger` was designed for and is not doing.

---

## Colour Studio, Animation Studio, and discoverability

Owner, verbatim: *"we have a thing somewhere in the menu that lets us choose
brand colors for all of the providers we have selected. I don't know where
to find it or how to do it."*

That is the whole finding. It exists — `settings_window.py`, the **"Agent
Colors"** card, one row per provider — and it is undiscoverable:

- **The brand colours are positional, not labelled.** Only the *first four*
  swatches in each row are Claude / OpenAI / Codex / Gemini brand colours,
  and the only way to learn that is a sentence of body text above the card
  plus a hover tooltip. Nothing on screen marks which four.
- **No provider identity on the row.** A row of anonymous squares does not
  say "this is Claude's colour"; it relies on remembering row order.
- **Two studios, no home.** Colour and animation editing are spread across
  cards inside one long settings pane, with no top-level "Studio" that says
  *this is where you make it yours*.
- **No before/after.** Changing a colour repaints a thumbnail, but there is
  no side-by-side of current vs proposed, and no way to preview on the
  actual strip before committing.

**What to build:**

1. **A named brand row.** Each provider's row leads with its own icon and
   name, then a labelled "Brand" group (the four official hues, each with a
   visible name), then custom swatches. Identity first, palette second.
2. **A real Studio surface** — one place, reachable from the menu bar, with
   Colours / Animations / Preview as peers. Settings keeps the toggles;
   the Studio owns the look.
3. **Live preview on both surfaces.** Hovering a swatch previews it on the
   Screen Bar and (opt-in) the strip, and reverts if not committed. This is
   also the fastest way to settle the green-gain question above.
4. **Per-provider animation, not just colour.** The owner asked for an
   Agents menu where each provider picks its own animation; the colour card
   is the natural home for it.
5. **Name every swatch, everywhere.** Anonymous colour squares were already
   called out in this file's own source comment as making the brand-colours
   tip "a lie". Fix the tip by fixing the swatches.
