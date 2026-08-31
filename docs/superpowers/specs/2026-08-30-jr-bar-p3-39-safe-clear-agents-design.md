# JR Bar P3.39 Safe Clear Agents design

Date: 2026-08-30

## Outcome

Replace the immediate `Clear Finished` mutation with a previewed, reversible
`Clear Agents...` action. The action acknowledges exact completed-event
presentation receipts owned by JR Bar. It never resets an agent, stops a
process, deletes a transcript, rewrites provider state, or changes history,
credentials, hooks, settings, or Other Macs configuration.

This is a presentation operation. Canonical operator truth remains the source
of live, waiting, failed, queued, and completed state.

## Safety boundary

The action may change only:

- exact local completion-presentation receipts;
- the most recent local Clear Agents batch receipt and its bounded Undo state.

The action must not change:

- canonical work, request, lifecycle, or event truth;
- live local request acknowledgements;
- active, waiting-input, waiting-permission, approval, queued, or failed rows;
- activity-ledger entries or operator-history entries;
- provider transcripts, status files, hook files, or process state;
- credentials, settings, provider profiles, remote-peer settings, or published
  peer ledgers;
- Notification Center deliveries or webhook deliveries that already occurred;
- LED, Screen Bar, announcer, finite-cue, or keep-awake runtimes directly.

The last group recomputes once from canonical survivors after a successful
commit. Current asks remain visible and keep their effects.

## Exact identity

`CompletionPresentationKey` is content-free and source-bound:

- `SourceKey` from `status.work_key.source_key`;
- agent ID;
- terminal event name;
- completion event timestamp in UTC epoch seconds.

The key is derived only from an eligible local `AgentStatus` in `COMPLETED`
mode, excluding `SessionEnd`, and only when the status carries a validated
`work_key`. A completion without exact source-bound key material is protected
instead of cleared. A receipt for one key cannot hide a newer completion,
reactivated session, later turn, colliding request ID, other provider, or other
source instance. The existing `cleared_session_ids` state and its reactivation
heuristic are removed.

## Pure model

Add `src/sidepulse/clear_agents.py` with immutable, bounded types:

- `CompletionPresentationKey`
- `CompletionPresentationReceipt`
- `ClearAgentsState`
- `ClearAgentsFence`
- `ClearAgentsPreviewItem`
- `ClearAgentsProtectedCounts`
- `ClearAgentsPreview`
- `ClearAgentsBatchReceipt`
- `ClearAgentsCommitPlan`
- `ClearAgentsUndoPlan`

`project_clear_agents_preview(...)` receives the already-reviewed eligible
completion statuses, current presentation rows, current receipt state, mailbox
receipt state, and relevant presentation generations. It returns exact
eligible items, protected counts, explicit preservation facts, and a typed
fence. Protected counts include remote completions and local completions that
lack exact source-bound identity. Persisted and rendered text never includes ask
text, transcript text, or raw paths.

`plan_clear_agents_commit(...)` accepts the preview, a freshly projected
preview, the current state, a bounded batch ID, and the commit time. It refuses
a changed fence, refuses an empty or invalid plan, adds only exact new receipts,
records one bounded batch receipt, and increments the state generation. It does
not mutate mailbox stable order or menu-visit seen state. That makes Undo exact:
removing the newly added overlay reveals a still-current row in its original
section and stable position.

`plan_clear_agents_undo(...)` accepts only the latest successful batch while
its five-minute Undo window is open. It removes only receipts newly added by
that batch, increments generation, and marks the batch undone. It never
reconstructs rows from a cached snapshot. Current canonical projection decides
what can reappear.

All collections are deterministically ordered and capped. Invalid or oversized
input fails closed.

## Persistence

Add `src/sidepulse/clear_agents_store.py` for one private, versioned,
content-free document named `clear-agents.json` in the existing state
directory. It stores:

- the bounded exact completion receipt overlay;
- the monotonic state generation;
- the latest bounded batch receipt and Undo deadline.

Decode requires exact fields and bounds. Unsupported, corrupt, unsafe, or
unavailable input restores an empty typed state with degraded health. Save uses
the existing private atomic-write boundary. No migration reads or writes the
old in-memory session-ID set.

Controller commits and undos use the existing serial persistence worker. The
controller adopts volatile state only after save success. Queue refusal or
write failure keeps the old state and shows generic local copy without paths or
exception details.

## Preview and stale-state fence

The preview fence contains semantic state, not a wall-clock-only snapshot:

- current Clear Agents state generation;
- exact eligible completion keys;
- protected presentation-row identity, lifecycle, and event-time signature;

Activity-ledger changes, announcer navigation, and mailbox visit bookkeeping do
not alter the clear target and therefore do not invalidate confirmation.

Confirmation reprojects from current state. Any mismatch refuses the commit,
refreshes the preview, and says that agents changed while the preview was open.
There is no partial clear against a stale preview.

Exact event receipts also make a delayed refresh harmless. A pre-clear
projection can be replaced once, but the successful apply invalidates memoized
mailbox/menu state and reprojects from the new receipt generation. A newer
event has a different key and appears immediately.

## Native interaction

Replace the root row with `Clear Agents...`, visible only when at least one
eligible local completed-event receipt has a source-bound key and is not
already acknowledged. It occupies the same slot and preserves the 15-row root
budget.

Use one native AppKit popover anchored to the status item. The preview shows:

- the count and bounded list of completed agents that will leave the current
  presentation;
- protected active, waiting, failed, and other current counts;
- that remote or unkeyed completions stay visible until exact identity is
  available;
- that exact local completion receipts are the only targets;
- that history, transcripts, hooks, credentials, settings, Other Macs, live
  asks, and failures stay.

Primary action copy is `Clear Presented Agents`. Secondary copy is `Cancel`.
After persistence succeeds, the same popover becomes a receipt with the exact
cleared count, the preservation statement, `Undo`, and `Done`. Saving, stale,
error, expired Undo, and successful Undo states are explicit. Controls expose
native accessibility labels, help, enabled state, focus order, Return, Tab,
Shift-Tab, and Escape behavior. After showing, the controller explicitly
activates the app, makes the popover window key, installs the first responder,
and uses a key-handling root view for deterministic traversal and dismissal.
The Clear Agents popover has a dedicated delegate or an identity-discriminated
close path, so it cannot trigger calibration-popover cleanup. Aqua, Dark Aqua,
Reduce Motion, and keyboard states are rendered by an isolated source-AppKit
receipt harness.

## Existing subsystem behavior

- Completion selection keeps the reviewed freshness, current-over-stale, and
  main-session rules.
- `completion_visibility.py` consumes exact receipt keys instead of session
  IDs. Its old clear planner is removed.
- The announcer stack keeps its independent Screen-Bar-only seen overlay. Safe
  Clear does not mark live asks seen.
- Local request triage keeps exact `RequestKey` behavior and its existing
  terminal-truth reconciliation. Safe Clear does not re-arm or delete live
  acknowledgements.
- Opening the menu remains the activity-ledger visit boundary. Clear Agents
  does not delete ledger entries or add a second definition of `seen`.
- Root action visibility uses a separate exact-receipt-backed
  `clearable_presented_count`. It never reuses the unread
  `unseen_finished_count`, which opening the menu intentionally consumes.
- Effects are not cancelled globally. One post-commit refresh recomputes the
  menu, LEDs, Screen Bar, announcer, and keep-awake state from canonical truth.

## Acceptance contract

1. No clearable completion means no root action and no no-op preview.
2. Mixed live asks and completions preview and clear only completions.
3. Waiting, approval, queued, failed, remote, unkeyed, and live acknowledged
   requests remain protected.
4. A changed clearable completion set, protected lifecycle signature, or Clear
   Agents receipt generation refuses confirmation and refreshes the preview.
5. Same agent, newer event reappears because its exact key differs.
6. Same request text or request ID on another source instance is untouched.
7. Restart restores acknowledged completion keys without restoring or deleting
   canonical state.
8. Undo removes only receipts newly added by the latest batch.
9. Expired, stale, repeated, or wrong-batch Undo is refused safely.
10. Store corruption, unsafe paths, queue refusal, and save failure retain the
    previous state and show generic copy.
11. History, transcript, hook, credential, settings, and remote configuration
    fixtures remain byte-identical through commit and Undo tests.
12. Current asks keep announcer, LED, notification, webhook, and power behavior.
13. Menu root remains at or below 15 rows.
14. Native previews and receipts render coherently in Aqua and Dark Aqua and
    expose keyboard and accessibility metadata.
15. The old `clearFinished_`, `plan_clear_finished`, `Clear Finished` row,
    stray `clearCompleted:` selector, and `cleared_session_ids` state have no
    production references.
16. An older persistence callback cannot overwrite a newer confirm or Undo.
17. Closing Clear Agents cannot run calibration-popover cleanup.

## Prior-art decisions

- T3 Code contributes reversible settle semantics, capability checks, and
  authoritative revalidation.
- CodexBar contributes generation fencing against delayed projections.
- T3Notch contributes exact turn/event-scoped acknowledgement rather than a
  session-ID tombstone.
- AgentBar and Huginn contribute execution-time owner/lifecycle rechecks and
  strict refusal to clear live work.
- agterm contributes the separation between ephemeral presentation attention
  and canonical session state.
- Original SidePulse PR 26 contributes only the user need and the idea of
  cancelling stale presentation. Its wholesale cache/session clear, lack of
  preview, and lack of Undo are not adopted.

No code is copied from GPL, BUSL, or unlicensed sources.

## Verification boundary

Run pure and store tests per implementation lane, then one combined P3.39
tranche. After review fixes, run all-source Ruff, `make fast`, and the complete
suite once at the batch boundary. Source-AppKit renders are source receipts,
not installed-app, live-provider, physical-hardware, signing, notarization,
publication, or release proof.
