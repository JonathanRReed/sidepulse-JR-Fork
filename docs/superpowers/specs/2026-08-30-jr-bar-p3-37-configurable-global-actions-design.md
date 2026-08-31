# JR Bar P3.37 Configurable Global Actions Design

**Date:** 2026-08-30
**Status:** Approved roadmap item, design ready for implementation
**Scope:** Recommendation 37, one configurable global action that reveals the
current ask or the existing control surface

## Outcome

JR Bar will provide one visible and configurable global action named **Reveal
Current Ask**. A registered shortcut invokes only that named action. It does not
observe, store, or suppress ordinary typing.

The action uses the existing announcer, answer-in-place, Screen Bar, and Agent
Browser paths:

1. If an actionable ask exists and its announcer can be presented, reveal the
   existing expanded announcer and preserve its selected request.
2. If that announcer is already expanded, collapse it.
3. If the Screen Bar surface is unavailable or no actionable ask exists, open
   the existing Agent Browser control surface.
4. Never enable Screen Bar, change triage, mark an ask seen, write mailbox
   state, send an answer, trigger a notification, or write physical LEDs merely
   because the global action fired.

The same action is always available as a visible JR Bar menu command. The menu
command and the global shortcut route through one controller method.

## Evidence Used

The current source has app-local menu equivalents and narrowly scoped
first-responder key handling, but no global or local event monitor. Screen Bar
ask presentation already belongs to `AnswerController`,
`AnnouncerStackPresentationBridge`, and `VirtualStatusDevice`. Settings already
preserve owned collections and unknown fields.

Current public prior art supports this shape:

- CodexBar registers one named reveal action and toggles its existing menu
  surface. Its Settings UI uses an explicit recorder state.
- T3 Code normalizes shortcuts, reports conflicts before save, preserves the
  previous binding when a replacement is invalid, and keeps actions visible in
  Settings rather than making them shortcut-only.
- Original SidePulse and the inspected fleet fork do not implement a global
  shortcut. Their useful pattern is exact selector routing and stable session
  identity, which JR Bar already extends.

Apple's current SDK still exports `RegisterEventHotKey` and
`UnregisterEventHotKey`. The API registers a discrete virtual-key-code and
modifier combination without inspecting ordinary key events. The API is not
thread safe, so registration, rebinding, callback dispatch, and teardown must
remain main-thread owned.

## Product Boundary

P3.37 includes:

- one action identifier, `reveal_current_ask`;
- one optional user-recorded shortcut;
- pure normalization and local conflict detection;
- a small Carbon registration adapter with an injectable backend;
- lifecycle-owned registration and idempotent teardown;
- a visible menu command;
- an Overview Settings row with record, clear, and status states;
- accessibility labels, help, keyboard recording, and Reduce Motion-safe
  presentation;
- source-native receipts and source tests.

P3.37 does not include:

- global text or key logging;
- `NSEvent.addGlobalMonitorForEvents`,
  `NSEvent.addLocalMonitorForEvents`, or `CGEventTap`;
- Accessibility or Input Monitoring permission requests;
- arbitrary user scripts, macros, or executable plugins;
- answer, approve, deny, clear, or destructive global actions;
- a complete inventory of shortcuts owned by other applications;
- automatic mutation of another application's binding;
- installed-app, signing, notarization, packaging, publication, or release
  claims.

## Pure Model

Create `src/sidepulse/global_actions.py` as an AppKit-free source of truth.

### Identities

```python
class GlobalActionID(str, Enum):
    REVEAL_CURRENT_ASK = "reveal_current_ask"

class ShortcutModifier(str, Enum):
    CONTROL = "control"
    OPTION = "option"
    SHIFT = "shift"
    COMMAND = "command"

@dataclass(frozen=True, slots=True)
class ShortcutChord:
    key_code: int
    key_label: str
    modifiers: frozenset[ShortcutModifier]
```

`key_code` is the macOS virtual key code used for registration. `key_label` is
bounded presentation text captured from `charactersIgnoringModifiers` while
the recorder owns first responder. The label is not used as execution
authority.

### Validation

A valid JR Bar global shortcut:

- has a key code from 0 through 127;
- has one printable key label of at most 16 characters;
- has at least one modifier;
- includes Command or Control;
- is not the unsupported pure Option plus Shift modifier pair;
- is not one of JR Bar's reserved responder-chain commands, including Quit,
  Close Window, Settings, Cut, Copy, Paste, Select All, Undo, and Redo;
- does not duplicate another JR Bar global action after normalization.

The first tranche has one global action, but the validator and registry accept
an action-to-chord mapping so future actions do not require a second shortcut
architecture.

### Status

```python
class GlobalActionBindingState(str, Enum):
    UNASSIGNED = "unassigned"
    ACTIVE = "active"
    LOCAL_CONFLICT = "local_conflict"
    UNSUPPORTED = "unsupported"
    REGISTRATION_REFUSED = "registration_refused"
    CLOSED = "closed"
```

The projected Settings status includes bounded user-facing text and a truthful
help string. `ACTIVE` means JR Bar registered the chord. It does not claim that
macOS exposes a complete cross-application conflict list.

## Persistence

Add one owned Settings collection:

```python
global_action_shortcuts: dict[str, dict] = field(default_factory=dict)
```

Only known action identifiers and validated chord fields become runtime
bindings. Unknown actions and malformed entries are ignored individually and
reported through the projected status. Unknown top-level settings remain
preserved by the existing compatibility layer.

New installs start unassigned. The visible menu action remains usable without a
shortcut. JR Bar will not silently claim a common system chord.

A user edit is transactional across both live registration and durable save:

1. Validate the candidate locally.
2. Prepare the candidate registration while the previous binding remains active
   and keep the candidate callback inert until commit.
3. Attempt to persist the candidate while the previous binding is still the
   committed runtime binding.
4. If persistence succeeds, commit the candidate callback, unregister the
   previous binding, and publish `ACTIVE`.
5. If registration or persistence fails, unregister the candidate, keep the
   previous active binding and persisted value, restore the committed Settings
   state, and show the bounded refusal or save reason.
6. Clear follows the same prepare, save, and commit boundary. A failed clear
   leaves the previous binding live and durable.

At application launch, a persisted binding is validated and registered. If
registration fails, the setting remains visible for repair but the action is
reported inactive.

## Native Registration Boundary

Create `src/sidepulse/global_hotkeys.py`.

The production adapter uses the Carbon functions exported by the macOS SDK:

- `GetApplicationEventTarget`;
- `InstallEventHandler` and `RemoveEventHandler`;
- `RegisterEventHotKey` and `UnregisterEventHotKey`;
- `GetEventParameter` for the bounded `EventHotKeyID` only.

The adapter must:

- load Carbon lazily on macOS;
- expose a small injectable backend for tests;
- install one application event handler;
- keep callback and registration references alive for their exact lifetime;
- map only the registered `EventHotKeyID` to a known action;
- dispatch the named action on the main thread;
- use non-exclusive registration so JR Bar does not suppress another app's
  shortcut;
- treat any nonzero `OSStatus` as a registration refusal;
- expose prepare, commit, and rollback operations so registration and Settings
  persistence form one transaction;
- close idempotently and unregister every owned reference exactly once;
- never log event text or unregistered keys.

Carbon's non-exclusive registration semantics cannot prove that another app
does not use the same chord. JR Bar reports local conflicts exactly and reports
system registration failures exactly, without claiming complete cross-app
conflict detection.

## AppKit Recording Boundary

Create `src/sidepulse/global_action_settings_pane.py` and embed its bounded row
in the existing Overview page. Do not add a new Settings category.

The recorder is a small first-responder AppKit view. It receives keys only while
the user has explicitly entered recording mode in the focused Settings window.
It does not install a global or local event monitor.

Recorder behavior:

- **Record Shortcut** enters recording and moves first responder to the
  recorder.
- The prompt becomes **Press Shortcut**.
- Escape cancels without changing the active binding.
- Delete or Backspace clears the binding after explicit recording begins.
- A valid modified key produces a candidate and exits recording.
- A local conflict remains unsaved and explains the conflicting command.
- A registration refusal keeps the previous binding and exposes the refusal.
- A save refusal or concurrent-write failure rolls back the candidate and
  restores the previous committed binding and recorder value.
- The current binding is displayed with standard modifier symbols and its
  captured key label.
- Success, Escape, clear, local conflict, registration refusal, and save failure
  all leave recording mode and restore the previous first responder or move
  focus to the known-safe **Record Shortcut** control.
- Outside recording mode, `keyDown_` delegates to `super` and never consumes
  text input.

Accessibility:

- the group label is `Global actions`;
- the recorder label is `Reveal current ask shortcut`;
- value is `Not set`, `Recording`, or the formatted chord;
- help explains that the action reveals the current ask or Agent Browser;
- conflict and registration status are exposed as static text and AX value;
- Tab enters the recorder and reaches Record and Clear in a stable loop;
- Reduce Motion uses immediate state changes.

## Controller and Presentation Flow

The retained controller owns one `GlobalHotkeyRegistry` because it already owns
application launch, settings refresh, menu commands, announcer reconciliation,
Agent Browser presentation, and termination.

Add one narrow controller method, `performRevealCurrentAsk_`, and one pure or
extracted coordinator helper if needed to preserve controller ratchets.

The action must use current canonical state at invocation time:

1. Reconcile and project the announcer stack.
2. Query a narrow public `VirtualStatusDevice.can_present_announcer()` method
   that owns the actual enabled, window-visible, display-awake, full-screen,
   Alcove, compact, and termination suppression truth.
3. If an actionable ask exists and Screen Bar presentation is available, reduce
   an exact current-generation `EXPAND` or `COLLAPSE` intent and resync the
   existing presenter.
4. Otherwise open the existing Agent Browser surface through its current
   presentation owner.
5. Do not synthesize request identity, use a cached stale generation, or create
   a second announcer window.

The menu item calls the same method and displays the current chord in bounded
secondary text. The global callback dispatches the same method on the main
thread.

## Lifecycle

- Construct the registry during controller initialization with an injected
  backend seam.
- Register the persisted binding once after AppKit launch has installed the
  main menu.
- Rebind only from explicit Settings edits or a settings refresh that changes
  the normalized chord.
- Close the registry once during application termination before native panels
  and workers are torn down.
- Fence late callbacks after close.
- Repeated launch, repeated settings refresh, repeated clear, and repeated
  termination are idempotent.

## Tests and Receipts

### Pure tests

- chord validation, formatting, ordering, and serialization;
- reserved JR Bar menu conflicts;
- duplicate action conflicts;
- unsupported and malformed persisted entries;
- honest binding-state projection.

### Registry tests

- exact action ID registration and callback routing;
- no ordinary key or event-text input;
- main-thread dispatch seam;
- transactional rebind and rollback;
- registration refusal preserves the prior binding;
- settings save refusal rolls back the prepared registration and preserves the
  prior runtime and durable binding;
- clear and close unregister exactly once;
- stale callbacks after rebind or close are ignored;
- forbidden monitoring API ratchet.

### Controller tests

- current ask expands through the existing announcer state;
- expanded announcer collapses;
- unavailable Screen Bar or no ask opens Agent Browser;
- disabled, hidden-window, full-screen, compact, Alcove-relevant, display-asleep,
  and terminating Screen Bar states all use the Agent Browser fallback;
- stale request generations are not reused;
- invocation does not write triage, seen receipts, mailbox, notification, answer,
  or LED state;
- launch registers once and termination closes once.

### Settings and native receipts

- settings round trip and unknown-field preservation;
- recorder keyboard and accessibility behavior;
- unset, recording, active, local-conflict, registration-refused, and cleared
  states in Aqua and Dark Aqua;
- source and image SHA binding, visible-text assertions, and AX readback.

### Batch gates

Run focused tests during each task. Once the source and receipts are stable,
run Ruff, `make fast`, one complete suite, one stable source/test fingerprint,
and a findings-first independent review.

## Evidence Boundary

P3.37 can prove pure shortcut rules, source-native AppKit recording, injected
Carbon lifecycle behavior, isolated source registration, controller routing,
and source test coverage. It cannot prove every cross-application conflict,
every keyboard layout, installed-app focus behavior, VoiceOver speech, signing,
notarization, packaging, publication, or release readiness.
