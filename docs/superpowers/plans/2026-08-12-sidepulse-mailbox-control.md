# SidePulse Mailbox Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add stable watch, pin, snooze, and woke behavior to the provider-neutral Agent Mailbox without hiding work that needs the user.

**Architecture:** A pure bounded preference projection owns ordering and visibility, while the existing AppKit controller persists private preferences and schedules one tracking-safe wake timer. The mailbox continues to consume authoritative lifecycle rows and never reclassifies raw payload text.

**Tech Stack:** Python 3.10+, frozen dataclasses, AppKit/PyObjC, existing private atomic JSON I/O, pytest, Ruff.

## Global Constraints

- Attention and actionable requests outrank watch, pin, and snooze.
- Snooze changes visibility only and never stops an agent.
- A fresh approval, input request, failure, or completion wakes a snoozed identity early.
- A failure or completion already visible when snooze begins remains snoozed.
- Malformed persisted state fails visible and does not hide an agent.
- Retain at most 100 primary identities and one one-shot wake timer.
- Never persist prompts, messages, tool arguments, commands, paths, account data, or raw errors.
- Do not add dependencies, install or restart the app, access real user data, or touch mounted hardware during source tasks.
- Do not commit unless the user separately authorizes commits.

---

### Task 1: Pure mailbox preference projection

**Files:**
- Create: `src/sidepulse/mailbox_preferences.py`
- Create: `tests/test_mailbox_preferences.py`
- Modify: `src/sidepulse/mailbox.py`
- Test: `tests/test_mailbox.py`

**Interfaces:**
- Consumes: `AgentMailboxProjection`, `MailboxRow`, and epoch seconds supplied by the controller.
- Produces:

```python
class MailboxPreferenceMode(str, Enum):
    DEFAULT = "default"
    WATCHED = "watched"
    PINNED = "pinned"

@dataclass(frozen=True, slots=True)
class MailboxPreference:
    agent_id: str
    mode: MailboxPreferenceMode = MailboxPreferenceMode.DEFAULT
    pin_order: int | None = None
    snoozed_at: float | None = None
    snoozed_until: float | None = None
    last_visited_at: float | None = None

@dataclass(frozen=True, slots=True)
class MailboxPreferenceProjection:
    projection: AgentMailboxProjection
    retained_preferences: tuple[MailboxPreference, ...]
    next_wake_epoch: float | None
    woke_agent_ids: tuple[str, ...]

def apply_mailbox_preferences(
    projection: AgentMailboxProjection,
    preferences,
    *,
    now: float,
) -> MailboxPreferenceProjection: ...
```

- [ ] **Step 1: Write failing pure tables**

Create literal rows for quiet, working, actionable, failed, and completed identities. Prove:

```python
def test_snooze_never_hides_actionable_rows(): ...
def test_running_row_stays_snoozed_until_deadline(): ...
def test_new_failure_after_snooze_wakes_early(): ...
def test_preexisting_failure_stays_snoozed(): ...
def test_new_completion_after_snooze_wakes_early(): ...
def test_malformed_snooze_fails_visible(): ...
def test_pin_order_is_stable_but_actionable_section_priority_wins(): ...
def test_watch_marks_row_without_activity_reordering(): ...
def test_woke_marker_clears_after_last_visited(): ...
def test_preferences_are_deduped_and_capped_at_one_hundred(): ...
```

- [ ] **Step 2: Capture RED**

Run:

```sh
PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_mailbox_preferences.py tests/test_mailbox.py -q
```

Expected: collection fails because `sidepulse.mailbox_preferences` is absent.

- [ ] **Step 3: Implement the smallest pure projection**

Canonicalize preference identities first. Treat nonfinite epochs, expired snoozes, `snoozed_until <= snoozed_at`, and epochs more than 366 days ahead as unsnoozed. A row is early-woken when it is actionable or when its failure or completion `updated_at` is later than `snoozed_at`. Preserve shelf priority, then pinned order, then existing stable mailbox order. Woke markers compare the triggering epoch with `last_visited_at`.

- [ ] **Step 4: Run pure and adversarial gates**

Add cases for duplicate preferences, tied timestamps, clock rollback, 1,000 source rows, and worker rollups. Run the Step 2 command and Ruff over the owned files.

- [ ] **Step 5: Write the Task 1 report**

Record the snooze truth table, RED/GREEN output, retention bound, privacy review, and AppKit work still pending in `.superpowers/sdd/2026-08-12-sidepulse-operator-experience/task-1-report.md`.

### Task 2: Private preference store and AppKit actions

**Files:**
- Create: `src/sidepulse/mailbox_preference_store.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `tests/test_sidepulse.py`
- Create: `tests/test_mailbox_preference_store.py`

**Interfaces:**
- Consumes: `apply_mailbox_preferences` and existing `atomic_private_write`, `read_private_text`, `RetentionPolicy` primitives.
- Produces:

```python
def load_mailbox_preferences(path: Path) -> tuple[MailboxPreference, ...]: ...
def save_mailbox_preferences(path: Path, preferences) -> None: ...

def watchMailboxAgent_(self, sender): ...
def pinMailboxAgent_(self, sender): ...
def snoozeMailboxAgent_(self, sender): ...
def unsnoozeMailboxAgent_(self, sender): ...
def mailboxWake_(self, timer): ...
```

- [ ] **Step 1: Write store and controller RED tests**

Use temporary paths and real AppKit menu items. Prove private modes, symlink/hard-link refusal through the existing private helper, corrupt-state visible fallback, atomic replace failure, exact snooze presets, selector routing, current item identity, one wake timer in common run-loop modes, early-wake reproject, last-visited clearing, and teardown.

- [ ] **Step 2: Capture RED**

Run:

```sh
PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_mailbox_preference_store.py tests/test_sidepulse.py -q -k 'mailbox or watch or pin or snooze or woke'
```

Expected: focused failures name the missing store, selectors, and timer.

- [ ] **Step 3: Implement persistence and controller ownership**

Store preferences below the existing private SidePulse state directory. Serialize only identity, enum mode, bounded pin order, snooze epochs, and last-visited epoch. Controller initialization loads once, every mutation saves atomically, and persistence failure leaves the in-memory dirty state visible for retry. Add one one-shot timer at the earliest future snooze deadline using `NSRunLoopCommonModes`.

- [ ] **Step 4: Integrate menu actions**

Add Watch, Pin, Snooze, and Unsnooze actions to each eligible primary mailbox row submenu. Do not offer Snooze for actionable rows. Presets are one hour, three hours, this evening when future, tomorrow morning, and next Monday morning. Use local calendar calculations but persist epochs.

- [ ] **Step 5: Run focused and neighboring gates**

Run:

```sh
/Users/jonathanreed/.local/share/sidepulse/venv/bin/ruff check src tests
PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_mailbox_preferences.py tests/test_mailbox_preference_store.py tests/test_mailbox.py tests/test_attention.py tests/test_sidepulse.py -q -k 'mailbox or ask or attention or watch or pin or snooze or woke or menu'
git diff --check -- src/sidepulse/mailbox_preferences.py src/sidepulse/mailbox_preference_store.py src/sidepulse/mailbox.py src/sidepulse/status_bar.py tests/test_mailbox_preferences.py tests/test_mailbox_preference_store.py tests/test_mailbox.py tests/test_sidepulse.py
```

- [ ] **Step 6: Independently review Task 2**

Review for stale actionable resurrection, snooze hiding pending work, timer churn, nonfinite clock data, stable ordering, private-state attacks, and menu tracking stability before marking complete.

### Task 3: Installed mailbox-control acceptance

**Files:**
- Modify: `.superpowers/sdd/2026-08-12-sidepulse-operator-experience/task-3-report.md`

**Interfaces:**
- Consumes: the built candidate app and real local agent statuses.
- Produces: source, package, installed UI, accessibility, and live-state evidence kept distinct.

- [ ] **Step 1: Run guarded repository checks**

Run the repo suite with the mounted-hardware guard active. Fix only proven test isolation or product regressions. Do not weaken the guard.

- [ ] **Step 2: Build a private candidate**

Use the existing verified packaging pipeline and a task-local output root. Verify bundle trust and signing state before launch. Do not install over the current app without explicit release authority.

- [ ] **Step 3: Verify rendered behavior**

Use a disposable synthetic high-volume fixture. Verify VoiceOver labels, row stability while open, all actions, timer wake, early wake, exact worker navigation, and no raw content. If the test harness cannot inject a disposable fixture without changing real state, report that boundary instead of using real user sessions.

- [ ] **Step 4: Record evidence**

Report source tests, package identity, rendered candidate behavior, and any installed or physical-device checks separately.
