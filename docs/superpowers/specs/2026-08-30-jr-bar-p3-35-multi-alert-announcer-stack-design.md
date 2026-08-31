# JR Bar P3.35 multi-alert announcer stack design

## Goal

Turn the Screen Bar announcer from one transient line into an honest, bounded
stack for simultaneous asks. It must expose the total count, the
highest-priority source, stable ordering, keyboard navigation, per-surface
acknowledgement, and a quiet collapsed state without changing the independent
LED notification contract.

This is a source and native-UI tranche. It does not authorize answering a
provider request in place, changing physical-device output, adding a global
hotkey, installing the application, publishing, or claiming installed-app
acceptance.

## Product boundary

JR Bar has three independent ambient surfaces:

- the Screen Bar announcer supplies words and transient navigation;
- the Glance Light or physical LED supplies persistent ambient attention;
- macOS notifications supply system-level interruption.

P3.35 changes only the Screen Bar announcer. Marking an item seen or dismissing
it on the Screen Bar never resolves the provider request, never writes local
operator triage, never clears a physical LED, and never marks a Notification
Center delivery handled. Only the canonical request leaving the actionable
projection clears the underlying ask and its LED state.

## Considered approaches

### A. Pure plan plus native collapsed and expanded panel, selected

Project immutable alert and stack state in an AppKit-free module. Keep the
collapsed window passive. Expand into a small, explicitly keyable native panel
only after direct user interaction. This gives stable ordering, deterministic
tests, real keyboard behavior, and a narrow AppKit adapter.

Cost: one additional pure module and one focused AppKit view module. Explicit
expansion temporarily accepts keyboard focus.

### B. Extend the existing text-only pill with cycling

This has less code, but a draw-only pill has no honest focus model, accessible
controls, selected-item semantics, or discoverable acknowledgement. It does
not satisfy the keyboard and accessibility requirements.

### C. Reuse Agent Browser

Agent Browser already has keyboard and accessibility patterns, but it is a
large management window with search, shelves, and actions. Reusing it would
turn a small ambient response into an unrelated workflow and would not provide
the unobtrusive collapsed state.

## Pure alert contract

`sidepulse.announcer_stack` owns the following AppKit-free types:

```python
class AnnouncerAlertPriority(IntEnum):
    PERMISSION = 0
    APPROVAL = 1
    REVIEW = 2
    INPUT = 3
    UNKNOWN = 4


class AnnouncerStackVisibility(str, Enum):
    HIDDEN = "hidden"
    COLLAPSED = "collapsed"
    EXPANDED = "expanded"


class AnnouncerStackAction(str, Enum):
    EXPAND = "expand"
    COLLAPSE = "collapse"
    PREVIOUS = "previous"
    NEXT = "next"
    OPEN = "open"
    MARK_SEEN = "mark_seen"


@dataclass(frozen=True, order=True, slots=True)
class AnnouncerAlertIdentity:
    value: str


@dataclass(frozen=True, slots=True)
class AnnouncerAlert:
    identity: AnnouncerAlertIdentity
    agent_id: str
    provider: str
    source_label: str
    session_label: str
    question: str
    priority: AnnouncerAlertPriority
    first_seen_sequence: int
    seen_on_screen_bar: bool


@dataclass(frozen=True, slots=True)
class AnnouncerStackState:
    ordered_identities: tuple[AnnouncerAlertIdentity, ...]
    first_seen_sequences: tuple[tuple[AnnouncerAlertIdentity, int], ...]
    priorities: tuple[tuple[AnnouncerAlertIdentity, AnnouncerAlertPriority], ...]
    seen_identities: frozenset[AnnouncerAlertIdentity]
    selected_identity: AnnouncerAlertIdentity | None
    expanded: bool
    next_sequence: int
    generation: int


@dataclass(frozen=True, slots=True)
class AnnouncerStackPlan:
    generation: int
    visibility: AnnouncerStackVisibility
    alerts: tuple[AnnouncerAlert, ...]
    selected_index: int | None
    total_actionable_count: int
    unseen_count: int
    highest_priority_source: str | None
    collapsed_text: str | None
    position_text: str | None
    accessibility_label: str
    accessibility_value: str
    accessibility_help: str
    can_previous: bool
    can_next: bool
    can_open: bool
    can_mark_seen: bool


@dataclass(frozen=True, slots=True)
class AnnouncerStackIntent:
    action: AnnouncerStackAction
    generation: int
    selected_identity: AnnouncerAlertIdentity | None
```

Public pure operations are:

- `reconcile_announcer_stack(previous, operator_state, actionable_rows, statuses)`;
- `expand_announcer_stack(state)`;
- `collapse_announcer_stack(state)`;
- `select_previous_announcer_alert(state)`;
- `select_next_announcer_alert(state)`;
- `mark_selected_announcer_alert_seen(state)`;
- `project_announcer_stack(state, operator_state, actionable_rows, statuses)`;
- `reduce_announcer_stack_intent(state, intent)` for presentation-only actions.

All values validate their exact types and bounds. Text is single-line and
bounded. The module contains no AppKit import, clock read, persistence, provider
mutation, or callback.

## Identity and stable order

Canonical `RequestKey` is the preferred identity. Its complete source instance,
work, and request components are encoded as compact, sorted-key JSON through
`request_key_to_payload`, then prefixed with `request:v1:`. A legacy row without
`RequestKey` falls back to a bounded identity derived from provider, agent,
session, semantic event, tool kind, and the opaque work key when one exists.
Legacy snapshots do not expose a trustworthy ask-open timestamp, so mutable
status refresh timestamps are excluded. The fallback is compatibility behavior,
not permission to infer identity from question text.

The canonical stack input is the current snapshot's optional
`CanonicalOperatorState`, the already-filtered actionable attention rows, and
the current snapshot statuses. It never uses the controller's retained
`current_operator_state` cache to decide whether canonical state is available,
because that cache may intentionally outlive a later unavailable snapshot.
Each request in `LIVE_UNACKNOWLEDGED` or `LIVE_ACKNOWLEDGED` phase with
`next_actor == USER` and whose work appears in the actionable rows becomes one
alert. This preserves the existing subagent and settings filters while keeping
one item and one count per request, even when one work owns several asks. A
current status may supply bounded question text and an exact open route only
when its `request_key` matches. The canonical state deliberately stores no
question content, so other canonical requests use `Needs your input` rather
than recollecting provider data or inventing content.

When canonical operator state is unavailable, legacy actionable attention rows
are projected one item per row through the fallback identity. This is a bounded
degraded path, not the normal source of request kind or cardinality.

Reconciliation follows these rules:

1. Remove identities whose requests are no longer actionable.
2. Retain every surviving identity in its previous relative position.
3. Append genuinely new identities in deterministic input order and assign each
   a monotonic first-seen sequence.
4. Never reorder a surviving identity because its message, timestamp, activity,
   or priority changed.
5. Refresh the immutable identity-to-priority map without changing order.
6. Prune seen receipts, sequence entries, and priority entries when their
   identity resolves.

This separates urgency from position. Permission requests can become the
default selected item without moving rows around.

## Priority and selection

Priority is semantic and fixed: permission, approval, review, general input,
then unknown actionable compatibility rows. Canonical request kind is
authoritative on the normal path. Legacy event and tool metadata map only
through a bounded table; question text is never classified.

When the current selection survives reconciliation, it remains selected. When
it does not survive, selection chooses the highest-priority unseen item, with
first-seen sequence and identity as stable tie-breakers. If all items have been
seen on the Screen Bar, selection falls back to the first stable item for
manual expanded inspection.

Previous and next navigation traverse the stable visual order and wrap at the
ends. Navigation changes selection only. It never marks an item seen and never
opens a session.

## Seen and acknowledgement semantics

P3.35 uses an in-memory, Screen-Bar-local seen receipt keyed by exact alert
identity. This is the product's per-surface acknowledgement for this tranche.

- `Mark Seen` adds only the selected identity to `seen_identities` and uses the
  state's current priority map to select the highest-priority unseen identity,
  with first-seen sequence and identity as tie-breakers.
- The request remains in the expanded stack while still actionable and is
  visibly labeled `Seen on Screen Bar`.
- Selection advances to the highest-priority unseen item without changing
  stable visual order.
- After every active identity is seen, the announcer becomes hidden. A newly
  arriving identity reopens the collapsed pill.
- Resolving an ask removes its receipt and identity during reconciliation.
- Restarting JR Bar intentionally reannounces unresolved asks. This avoids a
  hidden persistent receipt without adding storage or migration in this
  tranche.

Do not use `LocalTriageKind.ACKNOWLEDGE`, canonical acknowledged requests,
completion visit receipts, mailbox seen identities, or notification bindings.
Those contracts have different suppression and retention semantics.

The visible action is named `Mark Seen`, not `Acknowledge`, because it describes
the exact surface-local effect. Documentation may call this Screen Bar
acknowledgement.

## Collapsed presentation

The collapsed state is a passive, single-line pill under the Screen Bar. It
does not become key and does not activate JR Bar.

- One unseen ask uses the direct form `Source: question`, or
  `Source needs you` when the content is unavailable.
- Multiple unseen asks show the highest-priority unseen source and the truthful
  total: `Source needs you · N asks`.
- The visible count is the total actionable count, not only the unseen count.
- Counts above 99 display as `99+`, while the plan retains the exact integer.
- The source label and question keep the current 40, 80, and 140 character
  bounds and single-line normalization.
- No pulsing badge, continuous animation, translucent card stack, or large
  chrome is added.

For one item, clicking the announcer pill opens that item through the existing
`open_session(..., remember=False)` route. For multiple items, clicking expands
the stack so the choice is explicit. The Screen Bar's existing direct click
target remains a separate callback and continues to open the current oldest
ask. The announcer presenter never obtains its action handler from the Screen
Bar drawing view.

## Expanded presentation

The expanded state is a compact native panel anchored under the Screen Bar. It
shows one selected alert at a time rather than a scrolling list:

- source and session label;
- semantic priority label such as `Permission request`;
- bounded question or `Needs your input` fallback;
- stable position text such as `2 of 4`;
- total and unseen count;
- `Seen on Screen Bar` when applicable;
- native Previous, Next, Open, Mark Seen, and Close controls.

The panel width is bounded from 320 to 460 points and its content must fit
without horizontal clipping at standard macOS text sizes. The question uses a
native wrapping text field with at most two visible lines and tail truncation
after the 80-character content cap. Previous and Next may use compact native
arrow buttons, but their accessibility labels remain explicit. The panel uses
system typography, semantic colors, a 4 and 8 point spacing rhythm, and the
incumbent dark Screen Bar identity. Priority is communicated through words and
control state, never color alone.

## Keyboard and focus

The presenter uses an `NSPanel` subclass with borderless and
`NSWindowStyleMaskNonactivatingPanel` style masks. `canBecomeMainWindow` is
always false. `canBecomeKeyWindow` is true only while expanded after explicit
pointer interaction. The collapsed root refuses first responder. Expansion
calls `makeFirstResponder_` for the expanded root and `makeKeyWindow` without
calling any app-activation API. Collapse, suppression, hiding, and termination
resign key status. A new ask never invokes either key-window method.

While expanded:

- Left Arrow and Up Arrow select the previous item.
- Right Arrow and Down Arrow select the next item.
- Return opens the selected session.
- Space or `d` invokes Mark Seen.
- Escape collapses the panel.
- Tab traverses real native buttons with visible focus rings.

Every command copies `AnnouncerStackPlan.generation` and the selected identity
that the user can see. A stale button or key event becomes a no-op after
reconciliation. Collapsing resigns the
panel's key status and returns to the passive pill when unseen asks remain.

## Accessibility

The collapsed pill is one accessible `AXButton` element with:

- label `Screen Bar announcer`;
- value containing source, request type, count, and question;
- help explaining click behavior and that the LED notification remains active.

The expanded card is one `AXGroup` element with source, request type, position,
seen state, and question. Controls use native button roles and exact
labels: Previous Ask, Next Ask, Open Asking Session, Mark Seen on Screen Bar,
and Collapse Announcer. The expanded state exposes its position and count
without relying on geometry, color, or motion.

The view must remain usable under Increase Contrast and Differentiate Without
Color. Reduce Motion substitutes immediate show, hide, resize, and selection
updates for entrance or geometry animation. No keyboard command depends on
animation completion.

## AppKit ownership

`sidepulse.announcer_stack_view` owns the native panel and view classes.
`VirtualStatusDevice` owns one panel instance and only forwards the immutable
plan plus callbacks. `StatusBarController` owns the pure stack state because it
also owns actionable attention and session routing.

Controller flow:

1. Reconcile the current stack state from the current snapshot's optional
   operator state, `projection.actionable_attention`, and current statuses.
2. Project one immutable `AnnouncerStackPlan`.
3. Send the plan, which carries its state generation, and one typed,
   generation-fenced intent callback to `VirtualStatusDevice`.
4. Open intents resolve the exact current agent row and call the existing
   session opener.
5. Mark Seen updates only `AnnouncerStackState`, projects again, and resyncs the
   virtual device.
6. Resolution arrives through the next canonical attention projection and
   prunes the exact identity.

One `VirtualStatusDevice._announcer_allowed()` predicate covers Screen Bar
enabled state, termination, display sleep, a real visible host window,
`_fullscreen_hidden`, Alcove relevance, and compact mode. Both ordinary sync
and fullscreen reconciliation call this predicate through `_sync_announcer`.
Suppression hides presentation and resigns key status but does not mutate
selection or seen receipts.

The old `set_announcer_text` adapter and `project_announcer_content` formatter
remain temporarily for direct compatibility tests, including their existing
single-line and additional-ask wording. Production wiring uses the typed plan
and new copy. The compatibility adapter creates only a collapsed presentation
and contains no stack state.

## Motion and failure behavior

- Initial collapsed arrival may reuse the existing top-anchored entrance.
- Expansion and collapse use at most 180 milliseconds of ease-out geometry and
  alpha motion.
- Selection changes do not slide or carousel. Content replaces in place.
- Reduce Motion makes every update immediate.
- Failed AppKit animation setup applies the final frame and alpha immediately.
- Generation increments on every successful reconciliation, including a
  content-equivalent refresh, and on every accepted presentation intent.
  Missing, malformed, or stale callbacks are ignored without changing state.
- A malformed row is excluded from the announcer plan and never hides other
  valid asks.
- An empty plan closes or hides the panel and releases key status.
- Closing or terminating the Screen Bar releases callbacks and native windows.
- No render, input, or animation callback scans screen pixels, reads provider
  data, writes hardware, or performs disk I/O.

## Prior-art synthesis and attribution

The implementation is original Python and PyObjC code informed by these dated
sources:

- T3 Code at commit
  [`2daff8c`](https://github.com/pingdotgg/t3code/commit/2daff8c25adf701fddd062ae93b94cc57d420ec2),
  MIT, for separating status priority from stable creation order, collapsed
  counts, keyboard traversal, and local visit receipts.
- CodexBar at commit
  [`e8e2755`](https://github.com/steipete/CodexBar/commit/e8e275511105e6e76409f2ef308c9bbc8c2fbcdc),
  MIT, for pure layout planning, stable tie offsets, passive overlays, generation
  fencing, Reduce Motion, and grouped accessibility.
- SidePulse upstream at commit
  [`0445085`](https://github.com/inteliwear/sidepulse/commit/044508556934f913ac555d555e35e19b23294773),
  MIT, as the original product lineage and negative evidence for recency-based
  reordering.
- The SidePulse fleet fork at commit
  [`e5161c4`](https://github.com/adamstambouli/sidepulse/commit/e5161c47885e1246216a5dd98fa4317ad434ef7e),
  MIT fork, for sticky identity slots and coalescing behavior.
- T3Notch at commit
  [`f334abd`](https://github.com/zortos293/T3Notch/commit/f334abd225cd872b87b72a351800bc06ba064a7d),
  concept only. No license was published when inspected, so no code, assets,
  screenshots, wording, or distinctive implementation details may be copied.

`docs/PRIOR-ART.md` must record the exact snapshots and the P3.35 ideas before
the tranche closes.

## Tests and evidence

Source completion requires:

- table-driven pure tests for identity, priority, stable reconciliation,
  selection, wrap navigation, seen receipts, resolution pruning, count caps,
  copy bounds, invalid input, and generation increments;
- controller tests for typed plan wiring, exact session opening, surface-local
  Mark Seen, empty projection, and stale callback refusal;
- native panel tests for hidden, collapsed, expanded, multiple-item, all-seen,
  keyboard, focus, generation fencing, accessibility, suppression, cleanup,
  failed animation fallback, and Reduce Motion substitution;
- isolated AppKit renders in Aqua and Dark Aqua for one collapsed ask, multiple
  collapsed asks, expanded unseen selection, expanded seen selection, and
  Reduce Motion;
- visual inspection for clipping, contrast, stable anchoring, focus visibility,
  count truth, and non-color priority cues;
- Ruff, focused tests, `git diff --check`, `make fast`, one stable-fingerprint
  complete suite, and independent findings-first review.

Installed-app focus behavior, VoiceOver on the installed bundle, live provider
requests, physical LEDs, signing, notarization, and release evidence remain
separate gates.

## Non-goals

- No new production dependency.
- No answer-in-place controls or provider mutation.
- No global hotkey or ordinary-key interception.
- No persistent acknowledgement storage or migration.
- No change to local triage, mailbox, completion, or notification receipts.
- No physical LED, brightness, color, or effect change.
- No prompt, transcript, question history, or screen-content persistence.
- No Scene, DND, Clear Agents, away summary, sticky fleet band, or demo work;
  those remain later roadmap tranches.
