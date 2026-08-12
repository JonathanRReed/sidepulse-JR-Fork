# T3 Code findings — ideas worth stealing

Source: https://github.com/pingdotgg/t3code @ `5a84614` (2026-08-11),
read 2026-08-11. T3 Code is an "agent harness control surface" for
Claude Code / Codex / Cursor / Grok Build / OpenCode — the same
multi-provider problem SidePulse watches, with a best-in-class inbox.
File citations are t3code repo-relative.

## 1. An "ask" is a tracked request lifecycle, never a text guess

Their blocked-on-you state is two explicit booleans on every thread —
`hasPendingApprovals` and `hasPendingUserInput`
(`packages/contracts/src/orchestration.ts:461-462`) — derived by pairing
each approval/user-input REQUEST activity with a later RESOLUTION for
the same requestId (`apps/server/src/orchestration/decider.ts:33-56`).
No message-text sniffing anywhere.

**SidePulse adoption:** our phantom-ask came exactly from guessing.
We already track PermissionRequest events; the next step is splitting
ASK into two states — *Pending Approval* (tracked permission request,
strong signal, full escalation) and *Awaiting Input* (question
heuristic, softer signal) — and letting only the tracked kind escalate.

## 2. Sub-agents are not top-level entities

The thread (main session) is the ONLY unit. Sub-agent / fan-out turns
"ride along" inside their parent's detail window
(`orchestration.ts:575-577`), and live background work after the turn
settles is summarized on the parent as one field:
`backgroundLiveness: "working" | "monitoring"`
(`orchestration.ts:464-468`). Fleets of workers read as plain
*Working*; *Monitoring* is reserved for watch loops — "a parent agent
babysitting a PR" (`apps/web/src/components/Sidebar.logic.ts:677-698`).

**SidePulse adoption:** our new grouped-under-parent dropdown matches
the philosophy. Worth going further: roll worker count into the parent
row ("Working — 3 workers") and adopt the *Monitoring* distinction
(parent alive but only watching) as a calmer LED state than Working.

## 3. Color discipline: three colors, reserved

> "Four visual states, three colors: color is reserved for 'act now'
> (approval), 'in motion' (working), and 'broken' (failed). Ready is
> the unlabeled resting state."
(`apps/mobile/src/features/threads/threadListV2.ts:22-25`)

**SidePulse adoption:** the LED language should obey the same law —
saturated color means act-now, motion means working, red means broken,
and idle stays almost dark. Everything else is noise discipline.

## 4. One numeric priority ladder for the rollup

`Pending Approval(6) > Awaiting Input(5) > Working/Connecting(4) >
Plan Ready(3) > Monitoring(2) > Completed(1)` — and the project-level
indicator is simply the max (`Sidebar.logic.ts:134-146, 711-724`).
"A Monitoring sibling must never hide a Plan Ready thread."

**SidePulse adoption:** our precedence arbiter is the same instinct for
devices; the AGGREGATE (menu-bar icon + bar identity) should use one
explicit ladder too, with approval outranking everything.

## 5. "Did I see it" is modeled, not guessed

`hasUnseenCompletion`: the Completed pill shows only while the latest
turn's completion is NEWER than the client's `lastVisitedAt`
(`Sidebar.logic.ts:249-261`). Visiting the thread clears it — like
read/unread in mail.

**SidePulse adoption:** the exact fix for "I noticed you finished and
didn't see any sign of it": record when the dropdown was last opened,
and until then keep a green *unseen-done* dot on the menu-bar icon and
the session row. Opening the menu = visit = cleared.

## 6. Snooze that wakes itself when it matters

Snooze is visibility-only ("a running session IS snoozable — snooze
only affects visibility, never the agent"). A snoozed thread "raises
its hand" past the snooze when the agent becomes blocked on you, the
session fails FRESHLY (error newer than the snooze — "I saw it, not
now" stays snoozed), or a run completes after snoozing
(`packages/client-runtime/src/state/threadSettled.ts:96-140`). A "Woke"
indicator marks early wakes, cleared by visiting.

**SidePulse adoption:** per-session mute in the dropdown ("Quiet for an
hour") that auto-unmutes the moment that session actually needs you.
The escalation engine already has the hooks.

## 7. A settled lifecycle instead of ad-hoc aging

Threads auto-settle after N inactive days or instantly on a
merged/closed PR; an OPEN PR blocks auto-settle entirely; blocked work
can NEVER settle ("blocked work must remain visible even when a user
explicitly settled it"); explicit user override wins both ways; the
server un-settles on real activity (`threadSettled.ts:222-280`).
Settled/Snoozed live on collapsible shelves at the bottom of the list
(`threadListV2.ts:195-233`).

**SidePulse adoption:** our 24h prune is the crude cousin. A dropdown
"Clear" (settle) per finished session, plus the invariant that an
UNANSWERED ask never ages out of the Needs You section.

## 8. Grace windows are bounded on BOTH sides

A dispatched message no session has adopted counts as pending work for
at most 2 minutes — `Math.abs(now - messageAt)` so a skewed clock can't
pin the state (`threadSettled.ts:36-70`), and the server mirrors the
same constant (`decider.ts:28-30`).

**SidePulse adoption:** same trick for our own holds (UserPromptSubmit
→ working, completion glow windows): absolute-value the age so a bad
clock never wedges a state on.

## 9. Plan Ready is its own actionable state

A settled turn in plan mode with an actionable proposed plan gets a
distinct pill that "outranks lingering background work: it needs the
user's decision, while liveness merely reports"
(`Sidebar.logic.ts:663-677`).

**SidePulse adoption:** plan-approval moments (ExitPlanMode) are a
distinct signal — quieter than a permission ask, louder than working.

## 10. Working rows carry elapsed time

`resolveWorkingStartedAt` + `formatWorkingDurationLabel` ("4m",
"1h 12m") on every working row (`Sidebar.logic.ts:600-618`).

**SidePulse adoption:** dropdown session rows say "Working 4m" — pairs
naturally with our Timer feature.

## 11. The inbox sort is deliberately static

Rows do not re-sort as events arrive — "the thread reappears in its
original sort position (the inbox sort is deliberately static), so the
wake signal has to carry the weight" (`threadSettled.ts:183-189`).

**SidePulse adoption:** our dropdown re-sorts by priority+recency every
refresh, so rows jump under the cursor. Freeze ordering while the menu
is open; let dots/badges carry state changes.

## 12. Engineering practices worth copying

- **Client/server twins of every invariant**, in one shared package,
  so the UI can reject before a round trip and the server stays the
  authority (`threadSettled.ts:74-80`, `decider.ts:33-41`).
- **Pure `.logic.ts` modules** beside each view — every behavior above
  is a headless-testable pure function (we already do this; theirs is
  systematic).
- **Event-sourced shell stream** with sequence-deduped resume
  (`orchestration.ts:495-546`) — snapshot + `afterSequence` replay, no
  full refetch on reconnect.
- **Why-comments carrying incident context** — their comments read
  like our paid-for-in-blood ledger, at every decision point.

## Suggested adoption order

1. **Unseen-done via last-opened tracking** (#5) — smallest change,
   answers a complaint Jonathan already made.
2. **Ask split: Pending Approval vs Awaiting Input** (#1, #4) — kills
   the remaining phantom-ask class at the root.
3. **Static menu ordering while open + Working elapsed time** (#10,
   #11) — dropdown calm and glanceable.
4. **Monitoring state + worker count on the parent row** (#2).
5. **Session mute with raised-hand wake** (#6).
6. **Settle/Clear lifecycle** (#7) and **Plan Ready** (#9) as the
   bigger follow-ons.
