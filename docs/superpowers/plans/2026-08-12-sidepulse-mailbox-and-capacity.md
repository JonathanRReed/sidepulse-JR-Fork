# SidePulse Agent Mailbox and Capacity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:test-driven-development and superpowers:verification-before-completion. The controller uses superpowers:subagent-driven-development for task review. Do not commit because the repository owner has not authorized commits.

**Goal:** Replace a high-volume flat agent list with a stable lifecycle mailbox and make provider usage a reset-aware Capacity surface with truthful freshness, partial-result, and last-known-good behavior.

**Architecture:** Add two pure projections. The mailbox projection consumes the authoritative attention projection and current agent rows, then produces bounded stable shelves. The capacity policy normalizes provider windows and schedules display-only countdown updates and reset-boundary refreshes. Existing AppKit menu builders mutate stable menu items from those projections instead of reclassifying raw records.

**Tech Stack:** Python 3.10+, dataclasses, PyObjC/AppKit, pytest, Ruff.

## Global Constraints

- `Needs You` contains only actionable attention from `AttentionProjection`.
- A failure remains a visible row but never becomes a persistent Ask.
- Subagent attention obeys `subagent_asks_alert` everywhere.
- Activity changes never reorder rows within the same lifecycle shelf.
- Main agents are primary rows. Workers roll up under their parent or one orphan group.
- Each shelf exposes at most 12 primary rows and the projection retains at most 100 primary identities.
- Activity labels never include raw prompt, message, tool input, arguments, paths, tokens, authorization data, or arbitrary payload text.
- Usage percentages are clamped and booleans are rejected as numeric input.
- A successful explicit empty provider result clears old windows. Errors preserve only matching last-known-good provider data and mark it stale.
- Reset timers are provider-aware, bounded, coalesced, and cannot create overlapping refreshes.
- One provider or local transcript failure cannot prevent independent adapters from publishing their results.
- Do not add a production dependency.
- Do not commit, push, install, restart, deploy, write hardware, or mutate user state.
- Use strict RED before GREEN and record exact commands and output in each task report.

---

### Task 1: Pure Agent Mailbox projection

**Files:**
- Create: `src/sidepulse/mailbox.py`
- Create: `tests/test_mailbox.py`
- Read only: `src/sidepulse/attention.py`
- Read only: `src/sidepulse/models.py`

**Interfaces:**

```python
class MailboxSectionKind(str, Enum):
    NEEDS_YOU = "needs_you"
    IN_PROGRESS = "in_progress"
    READY_FOR_REVIEW = "ready_for_review"
    RECENT = "recent"

@dataclass(frozen=True, slots=True)
class MailboxRow:
    agent_id: str
    provider: str
    display_name: str
    lifecycle_mode: LifecycleMode
    activity_label: str | None
    actionable: bool
    navigation_agent_id: str | None
    worker_count: int
    updated_at: datetime
    stable_order: int

@dataclass(frozen=True, slots=True)
class MailboxSection:
    kind: MailboxSectionKind
    rows: tuple[MailboxRow, ...]
    overflow_count: int

@dataclass(frozen=True, slots=True)
class AgentMailboxProjection:
    sections: tuple[MailboxSection, ...]
    active_count: int
    needs_you_count: int
    ready_count: int
    retained_order: tuple[tuple[str, int], ...]
```

Add:

```python
def project_mailbox(
    projection: AttentionProjection,
    *,
    previous_order: Mapping[str, int] | None = None,
    seen_completion_ids: AbstractSet[str] = frozenset(),
    max_rows_per_section: int = 12,
    max_primary_agents: int = 100,
) -> AgentMailboxProjection: ...

def normalized_activity_label(status: AgentStatus) -> str | None: ...
```

- [ ] **Step 1: Write failing table tests that name the user-visible break**

Cover:

- live permission and input requests enter `Needs You`, oldest request first;
- terminal failures enter `Ready for Review` and remain non-actionable;
- working, tool-running, and long-task main rows enter `In Progress`;
- unseen completions enter `Ready for Review`, seen completions enter `Recent`;
- idle and unknown current rows enter `Recent` only when still present in the fresh projection;
- a tool-name change preserves row order when `previous_order` is reused;
- new rows append after existing rows within the same shelf;
- workers do not appear as primary rows and increment their parent's `worker_count`;
- orphan workers are represented by one deterministic synthetic rollup;
- each shelf returns 12 rows plus an exact overflow count;
- retention evicts expired recent identities before active or actionable identities;
- click targets come from projected actionable identity, not freshest raw timestamp;
- normalized labels map common read, edit, search, shell, and thinking tools without leaking raw `message`, `cwd`, or payload values;
- unknown tools are sanitized and length-bounded.

- [ ] **Step 2: Run and observe the missing-module RED**

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_mailbox.py -q`

Expected: collection failure because `sidepulse.mailbox` does not exist.

- [ ] **Step 3: Implement the minimum pure projection**

Use `ProjectedAgentRow.lifecycle_mode` as the semantic source. The mailbox module may read `source_status` only for display identity, parent relation, and sanitized tool categorization. It must not recompute actionability or classify `BLOCKED_ERROR` as Ask.

Preserve prior order for retained identities. Within one newly observed batch, allocate monotonically increasing integers in `(updated_at, agent_id)` order, then sort by stable order with `agent_id` as a deterministic tie breaker. Remove worker rows before section bounds are applied.

- [ ] **Step 4: Run the pure gate and Ruff**

Run:

`ruff check src/sidepulse/mailbox.py tests/test_mailbox.py`

Run the pytest command from Step 2.

Expected: all tests pass.

- [ ] **Step 5: Write the task report**

Include the RED receipt, GREEN count, exact shelf mapping, bounds, privacy rules, and any source field intentionally ignored.

### Task 2: AppKit Agent Mailbox integration

**Files:**
- Modify: `src/sidepulse/status_bar.py`
- Modify: `tests/test_sidepulse.py`
- Test: `tests/test_mailbox.py`

**Interfaces:**
- The controller retains mailbox stable order and seen-completion identity state.
- Add a stable top-level `Agent Mailbox` submenu builder and in-place updater.
- Reuse existing agent row and action submenu builders for navigation and actions.

- [ ] **Step 1: Write failing production-path menu tests**

Build real controller menu items with AppKit doubles and prove:

- the top-level summary reads `N active · M need you · K ready` from `AgentMailboxProjection`;
- shelves appear in the fixed order `Needs You`, `In Progress`, `Ready for Review`, `Recent`;
- opening or refreshing the menu does not reorder rows after activity changes;
- two main sessions with many workers produce two primary rows and bounded worker submenus, not one top-level item per worker;
- actionable rows keep existing jump behavior;
- failed and completed rows keep their existing action menus without acquiring Ask badges;
- an overflow shelf shows the exact `N more` count;
- the existing provider colors, names, and accessibility labels remain present;
- the old Ask Inbox and flat high-volume agent block are not duplicated alongside the mailbox.

- [ ] **Step 2: Run the focused controller tests and observe current flat-menu failures**

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_mailbox.py tests/test_sidepulse.py -q -k 'AgentMailbox or mailbox or ask_inbox or agent_monitor_menu or subagent_rollup'`

- [ ] **Step 3: Integrate one mailbox projection per refresh**

After the authoritative attention projection is built, call `project_mailbox` once with retained order and seen completion state. Use its result for both the top-level summary and submenu. Keep row actions delegated to existing helpers. Mark completion identity seen only through the existing user interaction or menu visibility semantics, not merely because a background refresh occurred.

Keep one top-level mailbox entry for all counts. Preserve a direct urgent indicator through its summary and existing status icon; do not create a second Ask list.

- [ ] **Step 4: Run mailbox, attention, completion, and high-volume menu gates**

Run Ruff over owned files.

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_mailbox.py tests/test_attention.py tests/test_completions.py tests/test_sidepulse.py -q -k 'mailbox or attention or ask or completion or subagent or provider_header or agent_monitor_menu'`

Expected: all selected checks pass.

- [ ] **Step 5: Write the task report**

Record RED and GREEN receipts, the stable-order lifecycle, row bounds, reused AppKit helpers, and remaining installed menu acceptance.

### Task 3: Reset-aware capacity policy and view models

**Files:**
- Create: `src/sidepulse/reset_policy.py`
- Modify: `src/sidepulse/usage_view.py`
- Modify: `src/sidepulse/refresh_policy.py`
- Modify: `tests/test_usage_view.py`
- Modify: `tests/test_refresh_policy.py`
- Create: `tests/test_reset_policy.py`

**Interfaces:**

```python
@dataclass(frozen=True, slots=True)
class ResetBoundaryPlan:
    deadline: float | None
    provider_ids: tuple[str, ...]
    boundary_keys: tuple[str, ...]

def parse_reset_epoch(value, *, now: float) -> float | None: ...
def format_reset_countdown(reset_epoch: float | None, *, now: float) -> str | None: ...
def next_countdown_deadline(reset_epochs, *, now: float) -> float | None: ...
def plan_reset_boundary_refresh(
    provider_windows,
    *,
    now: float,
    normal_refresh_deadline: float | None,
    attempted_boundary_keys: AbstractSet[str] = frozenset(),
    grace_seconds: float = 2.0,
    minimum_delay_seconds: float = 5.0,
) -> ResetBoundaryPlan: ...
```

Extend `UsageWindowViewModel` with:

- `reset_epoch: float | None`;
- `percent_remaining`;
- `reset_text(now: float) -> str | None`;
- `usage_known: bool`;
- `reset_known: bool`.

Extend `ProviderUsageViewModel` with:

- `partial: bool = False`;
- `source_text: str | None = None`.

- [ ] **Step 1: Write failing reset and presentation tables**

Use literal epoch fixtures to prove:

- ISO-8601 `Z`, offset ISO strings, integer seconds, float seconds, and numeric epochs at or above `100_000_000_000` interpreted as milliseconds normalize correctly;
- booleans, NaN, infinity, malformed strings, past resets, and resets more than 366 days ahead are omitted;
- countdown text is `now` for less than 60 seconds, then uses rounded-up whole minutes as `in Xm`, `in Xh Ym`, or `in Xd Xh` at exact boundaries;
- next countdown redraw lands on the next displayed-minute boundary and never causes provider fetch work;
- reset planning uses every provider window, groups providers that share the earliest boundary, adds grace, enforces the five-second minimum delay, skips attempted keys, and declines when normal refresh is due first;
- boundary keys include provider, semantic duration or label, and normalized reset epoch;
- attempted-key retention is deterministic and capped at 64;
- `percent_remaining` is `100 - percent_used` after clamping;
- explicit empty success and provider error last-known-good semantics remain unchanged;
- missing, loading, fresh, partial, stale, and error text remains truthful.

- [ ] **Step 2: Run and observe the missing reset-policy RED**

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_reset_policy.py tests/test_usage_view.py tests/test_refresh_policy.py -q`

- [ ] **Step 3: Implement pure normalization, formatting, and planning**

Use UTC parsing and the existing future-skew policy. A reset is useful only if it is plausibly in the future. Window ordering and provider ordering remain caller-supplied and deduplicated deterministically.

Do not infer a reset for transcript-only local summaries. Do not backfill reset values across providers, accounts, different semantic durations, or expired boundaries.

- [ ] **Step 4: Run the pure gate and Ruff**

Run Ruff over owned files, then rerun the Step 2 pytest command.

Expected: all selected checks pass.

- [ ] **Step 5: Write the task report**

Include the full reset table, bounds, malformed-value behavior, RED and GREEN output, and controller work still pending.

### Task 4: Capacity menu, reset timer, and countdown integration

**Files:**
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/settings_window.py`
- Modify: `tests/test_sidepulse.py`
- Test: `tests/test_reset_policy.py`
- Test: `tests/test_usage_view.py`
- Test: `tests/test_refresh_policy.py`

**Interfaces:**
- Rename the stable custom menu heading from `Usage` to `Capacity`.
- The controller owns one reset-boundary timer and one lightweight countdown timer.
- Reset callbacks route through `request_usage_refresh(provider_ids, reason="reset-boundary")`.

- [ ] **Step 1: Write failing production-path tests**

Prove:

- the Capacity card mutates in place while the menu is open;
- each provider shows a primary semantic window and remaining percentage plus secondary reset and age text;
- provider-local partial coverage remains visible with a bounded source summary and no raw path;
- stale and error last-known-good values stay visible with their state;
- a successful explicit empty result clears the provider's old capacity lines;
- local transcript scan failure cannot block successful Codex and Claude adapter windows;
- one provider failure cannot block or erase the other provider;
- menu open uses the existing planner and does not duplicate in-flight work;
- applying fresh windows schedules one earliest reset timer;
- reset callback requests only providers at that boundary and records attempted keys before requesting;
- an early or late timer callback reconciles against current windows and reschedules safely;
- settings or account invalidation clears mismatched reset timers and last-known reset values;
- countdown callback updates the stable card without calling transcript scan or quota adapters;
- provider and settings generations reject late worker results;
- all timer and attempted-key state is bounded and torn down with the controller.

- [ ] **Step 2: Run the focused controller tests and observe missing capacity behavior**

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_reset_policy.py tests/test_usage_view.py tests/test_refresh_policy.py tests/test_sidepulse.py -q -k 'Capacity or usage or quota or reset_boundary or countdown or ProviderAwareUsageRefreshTests'`

- [ ] **Step 3: Implement the stable Capacity card**

Reuse the current custom `NSView` item and update labels in place. Increase its height only as needed for two concise provider lines. Use project fonts, colors, and spacing. Do not add charts, gradients, badges, or generic dashboard chrome.

Settings copy must use semantic window terms and `Capacity`, not hard-coded weekly language when the provider reports a different duration.

- [ ] **Step 4: Integrate bounded timers with existing refresh state**

After applying provider results, derive a new reset plan from current normalized windows. Cancel and replace a timer only when its deadline or provider set changes. Reconcile early and late callbacks before triggering work. Record attempted keys with a 64-entry cap. The countdown timer only republishes the current view model at the next minute boundary.

Use existing request coalescing, retry, provider isolation, and generation fencing. Do not start new worker classes or another usage scan pipeline.

- [ ] **Step 5: Run capacity, usage, menu, and settings gates**

Run Ruff over owned files.

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_reset_policy.py tests/test_usage_view.py tests/test_refresh_policy.py tests/test_sidepulse.py -q -k 'capacity or usage or quota or reset or menu or settings'`

Expected: all selected checks pass.

- [ ] **Step 6: Write the task report**

Record RED and GREEN receipts, provider isolation, timer lifecycle, stable view mutation, exact copy changes, and remaining installed acceptance.

### Task 5: Transcript usage coverage and physical-source provenance

**Files:**
- Modify: `src/sidepulse/usage_stats.py`
- Modify: `src/sidepulse/status_bar.py`
- Modify: `src/sidepulse/usage_view.py`
- Create: `tests/test_usage_coverage.py`
- Test: `tests/test_freshness.py`
- Test: `tests/test_sidepulse.py`

**Interfaces:**

```python
class UsageSourceStatus(str, Enum):
    OK = "ok"
    MISSING = "missing"
    PARTIAL = "partial"
    FAILED = "failed"

@dataclass(frozen=True, slots=True)
class UsageSourceCoverage:
    provider_id: str
    status: UsageSourceStatus
    root_present: bool
    root_walked: bool
    files_discovered: int
    files_read: int
    cache_hits: int
    malformed_lines: int
    unreadable_files: int
    skipped_symlinks: int
    duplicate_physical_files: int
```

Add `source_coverage: dict[str, UsageSourceCoverage]` to `UsageTotals`.

- [ ] **Step 1: Write failing real-filesystem coverage tests**

Using temporary roots only, prove:

- absent Claude and Codex roots report `MISSING`, not zero-confidence `OK`;
- an existing empty root reports `OK` with zero files;
- readable valid files count as discovered and read;
- a warm unchanged file increments cache hits without another parse;
- a malformed usage candidate line increments malformed count and makes the provider `PARTIAL` while valid sibling totals remain;
- an unreadable or replaced file makes the provider `PARTIAL` without erasing valid sibling totals;
- a symlinked transcript is skipped and its external target is never opened;
- two paths to one physical inode are counted once and increment `duplicate_physical_files`;
- provider coverage is independent, so a partial Claude root does not mark Codex partial;
- cache persistence and pruning retain the existing Task 4 dirty-state and bounded-file contracts.

- [ ] **Step 2: Run and observe the missing-coverage RED**

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_usage_coverage.py tests/test_freshness.py -q`

- [ ] **Step 3: Implement coverage at the parsing boundary**

Return structured parse outcomes internally instead of collapsing read failure and valid empty files into `[]`. Discover entries with `lstat`, skip symlinks, and deduplicate regular files using `(st_dev, st_ino)` before opening. Persist only bounded non-sensitive counts and the existing normalized records. Do not persist or surface raw transcript content.

`PARTIAL` means at least one malformed candidate line or unreadable file while the root was walked. `FAILED` means the root exists but cannot be walked or no discovered file can be read because of failures. `MISSING` means the configured root is absent. Otherwise status is `OK`.

- [ ] **Step 4: Publish provider-local coverage to Capacity**

Pass the matching coverage object with each provider result. Build a bounded source label such as `Local transcripts · 241 files · partial`; never include a root or file path. An adapter-only quota result can remain fresh while its local transcript summary is partial, and the UI must say so.

- [ ] **Step 5: Run coverage, cache, usage, and controller gates**

Run Ruff over owned files.

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/test_usage_coverage.py tests/test_usage_view.py tests/test_refresh_policy.py tests/test_freshness.py tests/test_sidepulse.py -q -k 'coverage or usage or quota or cache or ProviderAwareUsageRefreshTests'`

Expected: all selected checks pass.

- [ ] **Step 6: Write the task report**

Record RED and GREEN receipts, real-filesystem fixtures, provider isolation, cache compatibility, privacy review, and any untested filesystem behavior.

### Task 6: Mailbox and Capacity integration verification

**Files:**
- Modify only if a regression test exposes a source defect in Tasks 1 through 4.
- Report: `.superpowers/sdd/2026-08-12-sidepulse-mailbox-and-capacity/task-6-report.md`

- [ ] **Step 1: Run complete guarded source gates**

Run:

`ruff check src tests`

Run:

`PYTHONPATH=src:. /Users/jonathanreed/.local/share/sidepulse/venv/bin/pytest tests/ -q`

Run:

`git diff --check`

The hardware guard must reject writes to every live SidePulse mount. Fix or isolate any test that attempts a live path. Do not weaken the guard.

- [ ] **Step 2: Perform final privacy and semantics review**

Check the final diff for raw activity leakage, independent attention reclassification, unstable mailbox ordering, unbounded rosters or timer sets, cross-provider reset backfill, stale-generation publication, and duplicate refresh work.

- [ ] **Step 3: Record the source receipt and live boundary**

List exact commands, counts, changed files, deferred findings, and all live checks still required. Installed verification must exercise a high-volume mailbox, a real menu-open refresh, displayed reset countdown rollover, provider error isolation, and reset-boundary refresh.
